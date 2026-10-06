"""Session issuance: access JWT + rotating opaque refresh token (httpOnly cookie)."""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Response, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import RefreshToken, User
from app.security import create_access_token, hash_token, new_opaque_token

REFRESH_COOKIE = "stem_refresh"
REFRESH_COOKIE_PATH = "/api/v1/auth"


def requires_mfa(user: User) -> bool:
    required = set(get_settings().mfa_required_roles)
    return any(r.role.value in required for r in user.roles)


async def issue_session(
    session: AsyncSession, response: Response, user: User, *, mfa: bool, user_agent: str | None
) -> dict:
    settings = get_settings()
    now = datetime.now(UTC)
    family = uuid.uuid4()
    raw = new_opaque_token(user.tenant_id)
    session.add(
        RefreshToken(
            tenant_id=user.tenant_id,
            user_id=user.id,
            token_hash=hash_token(raw),
            family_id=family,
            mfa_satisfied=mfa,
            last_used_at=now,
            expires_at=now + timedelta(hours=settings.refresh_token_idle_hours),
            absolute_expires_at=now + timedelta(days=settings.refresh_token_absolute_days),
            user_agent=(user_agent or "")[:400] or None,
        )
    )
    user.last_login_at = now
    _set_cookie(response, raw)
    return _token_body(user, mfa, family)


async def rotate_refresh(session: AsyncSession, response: Response, raw: str) -> tuple[User, dict]:
    settings = get_settings()
    now = datetime.now(UTC)
    token = await session.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw)))
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    if token.revoked_at is not None:
        # Re-use of a rotated token: assume theft, revoke the whole family.
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == token.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session revoked")
    if token.expires_at < now or token.absolute_expires_at < now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    user = await session.get(User, token.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account not active")

    new_raw = new_opaque_token(user.tenant_id)
    replacement = RefreshToken(
        tenant_id=user.tenant_id,
        user_id=user.id,
        token_hash=hash_token(new_raw),
        family_id=token.family_id,
        mfa_satisfied=token.mfa_satisfied,
        last_used_at=now,
        expires_at=min(now + timedelta(hours=settings.refresh_token_idle_hours), token.absolute_expires_at),
        absolute_expires_at=token.absolute_expires_at,
        user_agent=token.user_agent,
    )
    session.add(replacement)
    await session.flush()
    token.revoked_at = now
    token.replaced_by_id = replacement.id
    _set_cookie(response, new_raw)
    return user, _token_body(user, token.mfa_satisfied, token.family_id)


async def revoke_family(session: AsyncSession, raw: str) -> None:
    token = await session.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw)))
    if token is not None:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == token.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )


def _token_body(user: User, mfa: bool, family: uuid.UUID) -> dict:
    settings = get_settings()
    return {
        "access_token": create_access_token(
            user_id=user.id, tenant_id=user.tenant_id, mfa=mfa, session_family=family
        ),
        "token_type": "bearer",
        "expires_in": settings.access_token_minutes * 60,
    }


def _set_cookie(response: Response, raw: str) -> None:
    settings = get_settings()
    response.set_cookie(
        REFRESH_COOKIE,
        raw,
        httponly=True,
        secure=settings.environment in ("production", "staging"),
        samesite="strict",
        path=REFRESH_COOKIE_PATH,
        max_age=settings.refresh_token_absolute_days * 86400,
    )


def clear_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)
