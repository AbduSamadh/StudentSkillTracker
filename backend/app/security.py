"""Password hashing, tokens, TOTP and field-level encryption."""

import base64
import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.config import get_settings

_hasher = PasswordHasher()
JWT_ALG = "HS256"
JWT_AUDIENCE = "stemtrack-api"


# ---- Passwords ----
def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        # Spend comparable time so missing accounts are not distinguishable by timing.
        _hasher.hash(password)
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


# ---- Opaque tokens (refresh, magic link, opt-out) ----
def new_opaque_token(tenant_id: uuid.UUID) -> str:
    """Tokens embed the tenant so the tenant context can be set before lookup."""
    return f"{tenant_id.hex}.{secrets.token_urlsafe(32)}"


def split_opaque_token(token: str) -> tuple[uuid.UUID, str] | None:
    try:
        tenant_hex, _ = token.split(".", 1)
        return uuid.UUID(hex=tenant_hex), token
    except (ValueError, AttributeError):
        return None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---- JWT access tokens ----
def create_access_token(
    *, user_id: uuid.UUID, tenant_id: uuid.UUID, mfa: bool, session_family: uuid.UUID | None = None
) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "tid": str(tenant_id),
        "mfa": mfa,
        "aud": JWT_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
        "jti": uuid.uuid4().hex,
    }
    if session_family:
        payload["sfam"] = str(session_family)
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALG)


def decode_access_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, get_settings().jwt_secret, algorithms=[JWT_ALG], audience=JWT_AUDIENCE)


def create_scoped_token(purpose: str, data: dict[str, Any], minutes: int) -> str:
    """Short-lived signed token for a single purpose (MFA enrolment step, opt-out link)."""
    now = datetime.now(UTC)
    payload = {
        **data,
        "purpose": purpose,
        "aud": JWT_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
    }
    return jwt.encode(payload, get_settings().jwt_secret, algorithm=JWT_ALG)


def decode_scoped_token(token: str, purpose: str) -> dict[str, Any]:
    data = jwt.decode(token, get_settings().jwt_secret, algorithms=[JWT_ALG], audience=JWT_AUDIENCE)
    if data.get("purpose") != purpose:
        raise jwt.InvalidTokenError("wrong purpose")
    return data


# ---- TOTP MFA ----
def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, account: str, issuer: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=issuer)


def verify_totp(secret: str, code: str) -> bool:
    return pyotp.TOTP(secret).verify(code.strip(), valid_window=1)


# ---- Field-level encryption ----
def _fernet() -> MultiFernet:
    # Encrypts with the current key; decrypts with the current key or any retired one.
    s = get_settings()
    return MultiFernet(
        [Fernet(k.encode()) for k in (s.field_encryption_key, *s.field_encryption_previous_keys)]
    )


def encrypt_field(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return _fernet().encrypt(value.encode()).decode()


def decrypt_field(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        return None


def rotate_field(value: str | None) -> str | None:
    """Re-encrypt a stored value under the current key. Raises InvalidToken if no configured key
    can read it, so a rotation never silently destroys data."""
    if not value:
        return value
    return _fernet().rotate(value.encode()).decode()


def blind_index(value: str | None) -> str | None:
    """Deterministic keyed hash so an encrypted email can be looked up without decrypting."""
    if not value:
        return None
    normalised = value.strip().lower()
    digest = hmac.new(get_settings().blind_index_key.encode(), normalised.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def sign_payload(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
