import asyncio
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select

from app import audit
from app.config import get_settings
from app.db import anonymous_session, tenant_session
from app.deps import CtxDep
from app.models import Guardian, MagicLink, RoleAssignment, Student, Tenant, User
from app.models.enums import Role, ScopeType
from app.permissions import CAPABILITIES
from app.ratelimit import client_ip, limit_auth
from app.schemas.types import Email
from app.security import (
    blind_index,
    create_scoped_token,
    decode_scoped_token,
    decrypt_field,
    encrypt_field,
    hash_token,
    new_opaque_token,
    new_totp_secret,
    split_opaque_token,
    totp_uri,
    verify_password,
    verify_totp,
)
from app.services import auth as auth_service
from app.services.channels import send_email
from app.tenancy import load_settings

router = APIRouter(prefix="/auth", tags=["auth"])


class TokenOut(BaseModel):
    status: Literal["ok", "mfa_required", "mfa_enrolment_required"] = "ok"
    access_token: str | None = None
    token_type: str = "bearer"  # noqa: S105
    expires_in: int | None = None
    login_token: str | None = None  # for the MFA step


class PasswordLogin(BaseModel):
    tenant: str
    email: Email
    password: str


class MfaStep(BaseModel):
    login_token: str
    code: str


class MfaEnrolOut(BaseModel):
    secret: str
    otpauth_uri: str


class OidcCallback(BaseModel):
    code: str
    state: str


class ParentLinkRequest(BaseModel):
    tenant: str
    email: Email


class ParentVerify(BaseModel):
    token: str


async def _tenant_by_slug(slug: str) -> Tenant:
    async with anonymous_session() as s:
        tenant = await s.scalar(select(Tenant).where(Tenant.slug == slug))
    if tenant is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    return tenant


def _audit_login(session, user: User, action: str, **context) -> None:  # noqa: ANN001
    audit.record(
        session,
        actor_user_id=user.id,
        actor_roles=sorted({r.role.value for r in user.roles}),
        action=action,
        subject_type="user",
        subject_id=user.id,
        context=context,
    )


async def _complete_first_factor(session, response: Response, user: User, request: Request, mfa: bool) -> TokenOut:  # noqa: ANN001
    if auth_service.requires_mfa(user) and not mfa:
        token = create_scoped_token("login_mfa", {"uid": str(user.id), "tid": str(user.tenant_id)}, 5)
        await session.commit()
        if not user.mfa_enabled:
            return TokenOut(status="mfa_enrolment_required", login_token=token)
        return TokenOut(status="mfa_required", login_token=token)
    body = await auth_service.issue_session(
        session, response, user, mfa=mfa, user_agent=request.headers.get("user-agent")
    )
    _audit_login(session, user, "auth.login", mfa=mfa)
    await session.commit()
    return TokenOut(**body)


@router.post("/password-login", response_model=TokenOut, dependencies=[Depends(limit_auth)])
async def password_login(body: PasswordLogin, request: Request, response: Response) -> TokenOut:
    """Password sign-in for development and for schools without an IdP. Staff at schools
    with SSO configured should use /auth/oidc/start."""
    tenant = await _tenant_by_slug(body.tenant)
    async with tenant_session(tenant.id) as session:
        user = await session.scalar(select(User).where(User.email == body.email.lower()))
        # Always hash, so a missing account costs the same time as a wrong password.
        password_ok = verify_password(body.password, user.password_hash if user else None)
        if user is None or not user.is_active or not password_ok:
            if user is not None:
                _audit_login(session, user, "auth.login_failed")
                await session.commit()
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
        return await _complete_first_factor(session, response, user, request, mfa=False)


def _login_token(token: str) -> tuple[uuid.UUID, uuid.UUID]:
    try:
        data = decode_scoped_token(token, "login_mfa")
        return uuid.UUID(data["uid"]), uuid.UUID(data["tid"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign-in step expired; start again") from exc


@router.post("/mfa/enrol", response_model=MfaEnrolOut, dependencies=[Depends(limit_auth)])
async def mfa_enrol(body: ParentVerify) -> MfaEnrolOut:
    """Start TOTP enrolment after a successful first factor (body.token = login_token)."""
    user_id, tenant_id = _login_token(body.token)
    async with tenant_session(tenant_id) as session:
        user = await session.get(User, user_id)
        tenant = await session.get(Tenant, tenant_id)
        if user is None or tenant is None or user.mfa_enabled:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enrolment not available")
        secret = new_totp_secret()
        user.mfa_secret_enc = encrypt_field(secret)
        await session.commit()
        return MfaEnrolOut(secret=secret, otpauth_uri=totp_uri(secret, user.email, tenant.name))


@router.post("/mfa/verify", response_model=TokenOut, dependencies=[Depends(limit_auth)])
async def mfa_verify(body: MfaStep, request: Request, response: Response) -> TokenOut:
    """Second factor: verifies a TOTP code (and activates enrolment on first use)."""
    user_id, tenant_id = _login_token(body.login_token)
    async with tenant_session(tenant_id) as session:
        user = await session.get(User, user_id)
        secret = decrypt_field(user.mfa_secret_enc) if user else None
        if user is None or secret is None or not verify_totp(secret, body.code):
            if user is not None:
                _audit_login(session, user, "auth.mfa_failed")
                await session.commit()
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid code")
        if not user.mfa_enabled:
            user.mfa_enabled = True
            _audit_login(session, user, "auth.mfa_enrolled")
        body_out = await auth_service.issue_session(
            session, response, user, mfa=True, user_agent=request.headers.get("user-agent")
        )
        _audit_login(session, user, "auth.login", mfa=True)
        await session.commit()
        return TokenOut(**body_out)


# ---------------- Staff SSO (OIDC) ----------------
async def _discovery(issuer: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(issuer.rstrip("/") + "/.well-known/openid-configuration")
        r.raise_for_status()
        return r.json()


@router.get("/oidc/start")
async def oidc_start(tenant: str) -> dict:
    t = await _tenant_by_slug(tenant)
    oidc = load_settings(t.settings).oidc
    if not oidc.issuer or not oidc.client_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SSO is not configured for this school")
    disco = await _discovery(oidc.issuer)
    nonce = secrets.token_urlsafe(16)
    state = create_scoped_token("oidc_state", {"tid": str(t.id), "nonce": nonce}, 10)
    params = {
        "client_id": oidc.client_id,
        "response_type": "code",
        "scope": "openid email profile",
        "redirect_uri": f"{get_settings().public_base_url}/auth/callback",
        "state": state,
        "nonce": nonce,
    }
    return {"authorization_url": f"{disco['authorization_endpoint']}?{urlencode(params)}"}


@router.post("/login", response_model=TokenOut, dependencies=[Depends(limit_auth)])
async def oidc_login(body: OidcCallback, request: Request, response: Response) -> TokenOut:
    """Staff SSO callback: exchanges the authorisation code with the school's IdP."""
    try:
        state = decode_scoped_token(body.state, "oidc_state")
        tenant_id = uuid.UUID(state["tid"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid state") from exc
    async with tenant_session(tenant_id) as session:
        tenant = await session.get(Tenant, tenant_id)
        assert tenant is not None
        oidc = load_settings(tenant.settings).oidc
        if not oidc.issuer or not oidc.client_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "SSO is not configured")
        disco = await _discovery(oidc.issuer)
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                disco["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": body.code,
                    "redirect_uri": f"{get_settings().public_base_url}/auth/callback",
                    "client_id": oidc.client_id,
                    "client_secret": decrypt_field(oidc.client_secret_enc) or "",
                },
            )
        if r.status_code != 200:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Identity provider rejected the sign-in")
        id_token = r.json().get("id_token", "")
        jwks = jwt.PyJWKClient(disco["jwks_uri"])
        signing_key = await asyncio.to_thread(jwks.get_signing_key_from_jwt, id_token)
        try:
            claims = jwt.decode(
                id_token, signing_key.key, algorithms=["RS256", "ES256"], audience=oidc.client_id, issuer=disco["issuer"]
            )
        except jwt.PyJWTError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid identity token") from exc
        if claims.get("nonce") != state["nonce"]:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid nonce")
        email = (claims.get("email") or claims.get("preferred_username") or "").lower()
        user = await session.scalar(select(User).where(User.oidc_subject == claims["sub"]))
        if user is None and email:
            user = await session.scalar(select(User).where(User.email == email))
            if user is not None:
                user.oidc_subject = claims["sub"]
        if user is None or not user.is_active:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your account has not been set up on this platform")
        mfa = bool(set(claims.get("amr") or []) & set(oidc.mfa_amr_values))
        return await _complete_first_factor(session, response, user, request, mfa=mfa)


# ---------------- Parents: magic link ----------------
@router.post("/parent/request-link", status_code=202, dependencies=[Depends(limit_auth)])
async def parent_request_link(body: ParentLinkRequest, request: Request) -> dict:
    """Always 202 so the endpoint cannot be used to discover which emails are registered."""
    try:
        tenant = await _tenant_by_slug(body.tenant)
    except HTTPException:
        return {"ok": True}
    settings = get_settings()
    async with tenant_session(tenant.id) as session:
        guardians = (await session.scalars(select(Guardian).where(Guardian.email_hash == blind_index(body.email)))).all()
        if not guardians:
            return {"ok": True}
        email = body.email.lower()
        user = await session.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email, display_name=guardians[0].full_name, locale=guardians[0].language)
            session.add(user)
            await session.flush()
        existing = {(r.role, r.scope_id) for r in user.roles}
        for g in guardians:
            g.user_id = user.id
            if (Role.PARENT, g.id) not in existing:
                session.add(
                    RoleAssignment(user_id=user.id, role=Role.PARENT, scope_type=ScopeType.GUARDIAN, scope_id=g.id)
                )
        raw = new_opaque_token(tenant.id)
        session.add(
            MagicLink(
                user_id=user.id,
                token_hash=hash_token(raw),
                expires_at=datetime.now(UTC) + timedelta(minutes=settings.magic_link_minutes),
                requested_ip=client_ip(request),
            )
        )
        link = f"{settings.public_base_url}/portal/login?token={raw}"
        ar = guardians[0].language.value == "ar"
        subject = "رابط الدخول إلى بوابة أولياء الأمور" if ar else "Your parent portal sign-in link"
        text = (
            f"يُرجى استخدام هذا الرابط لتسجيل الدخول خلال {settings.magic_link_minutes} دقيقة:\n{link}"
            if ar
            else f"Use this link to sign in within {settings.magic_link_minutes} minutes:\n{link}"
        )
        await send_email(session, email, subject, text, meta={"kind": "magic_link"})
        audit.record(session, actor_user_id=user.id, actor_roles=["parent"], action="auth.magic_link_requested",
                     subject_type="user", subject_id=user.id)
        await session.commit()
    return {"ok": True}


@router.post("/parent/verify", response_model=TokenOut, dependencies=[Depends(limit_auth)])
async def parent_verify(body: ParentVerify, request: Request, response: Response) -> TokenOut:
    parsed = split_opaque_token(body.token)
    if parsed is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid link")
    tenant_id, raw = parsed
    async with tenant_session(tenant_id) as session:
        link = await session.scalar(select(MagicLink).where(MagicLink.token_hash == hash_token(raw)))
        now = datetime.now(UTC)
        if link is None or link.used_at is not None or link.expires_at < now:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This link has expired. Request a new one.")
        link.used_at = now
        user = await session.get(User, link.user_id)
        assert user is not None
        body_out = await auth_service.issue_session(
            session, response, user, mfa=False, user_agent=request.headers.get("user-agent")
        )
        _audit_login(session, user, "auth.login", method="magic_link")
        await session.commit()
        return TokenOut(**body_out)


# ---------------- Session management ----------------
@router.post("/refresh", response_model=TokenOut)
async def refresh(
    response: Response,
    stem_refresh: str | None = Cookie(default=None),
    x_requested_with: str | None = Header(default=None),
) -> TokenOut:
    # Custom header + SameSite=Strict cookie defeats cross-site refresh attempts.
    if x_requested_with != "stemtrack":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing X-Requested-With header")
    parsed = split_opaque_token(stem_refresh or "")
    if parsed is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No session")
    tenant_id, raw = parsed
    async with tenant_session(tenant_id) as session:
        _, body = await auth_service.rotate_refresh(session, response, raw)
        await session.commit()
        return TokenOut(**body)


@router.post("/logout", status_code=204)
async def logout(response: Response, stem_refresh: str | None = Cookie(default=None)) -> Response:
    parsed = split_opaque_token(stem_refresh or "")
    if parsed is not None:
        async with tenant_session(parsed[0]) as session:
            await auth_service.revoke_family(session, parsed[1])
            await session.commit()
    auth_service.clear_cookie(response)
    response.status_code = 204
    return response


class MeOut(BaseModel):
    user_id: uuid.UUID
    display_name: str
    email: str
    locale: str
    roles: list[dict]
    capabilities: list[str]
    mfa: bool
    tenant: dict
    children: list[dict]
    student_portal: dict | None


@router.get("/me", response_model=MeOut)
async def me(ctx: CtxDep) -> MeOut:
    p = ctx.principal
    user = await ctx.session.get(User, p.user_id)
    assert user is not None
    settings = ctx.settings
    children: list[dict] = []
    if p.guardian_ids:
        from app.permissions import family_student_ids

        ids = await family_student_ids(ctx.session, p)
        for s in (await ctx.session.scalars(select(Student).where(Student.id.in_(ids)))).all():
            children.append({"id": s.id, "name": s.display_name, "year_group": s.year_group})
    student_portal = None
    if p.own_student_ids:
        sid = next(iter(p.own_student_ids))
        st = await ctx.session.get(Student, sid)
        if st is not None:
            student_portal = {
                "student_id": st.id,
                "enabled": st.year_group >= settings.student_portal_min_year,
            }
    return MeOut(
        user_id=p.user_id,
        display_name=p.display_name,
        email=p.email,
        locale=user.locale.value,
        roles=[{"role": a.role.value, "scope_type": a.scope_type.value, "scope_id": a.scope_id} for a in p.assignments],
        capabilities=sorted(c.value for c in CAPABILITIES if p.can(c)),
        mfa=p.mfa,
        tenant={
            "id": ctx.tenant.id,
            "slug": ctx.tenant.slug,
            "name": ctx.tenant.name,
            "name_ar": ctx.tenant.name_ar,
            "branding": settings.branding.model_dump(),
            "timezone": settings.timezone,
        },
        children=children,
        student_portal=student_portal,
    )


@router.get("/tenant/{slug}")
async def tenant_public(slug: str) -> dict:
    """Public branding for the login screen."""
    async with anonymous_session() as s:
        t = await s.scalar(select(Tenant).where(Tenant.slug == slug))
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown school")
    st = load_settings(t.settings)
    return {
        "name": t.name,
        "name_ar": t.name_ar,
        "branding": st.branding.model_dump(),
        "sso_enabled": bool(st.oidc.issuer and st.oidc.client_id),
    }
