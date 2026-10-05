"""Request context: authenticated principal + tenant-scoped database session."""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit, jobs
from app.config import get_settings
from app.db import tenant_session
from app.models import Tenant, User
from app.permissions import Assignment, Cap, Principal
from app.ratelimit import client_ip, hit
from app.security import decode_access_token
from app.tenancy import TenantSettings, load_settings


@dataclass
class Ctx:
    session: AsyncSession
    principal: Principal
    tenant: Tenant
    deferred: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def defer(self, job: str, **kwargs: Any) -> None:
        """Run a background job once this request's transaction has committed."""
        if (job, kwargs) not in self.deferred:
            self.deferred.append((job, kwargs))

    @property
    def settings(self) -> TenantSettings:
        return load_settings(self.tenant.settings)

    @property
    def user_id(self) -> uuid.UUID:
        return self.principal.user_id

    def require(self, cap: Cap) -> None:
        self.principal.require(cap)

    def audit(
        self,
        action: str,
        subject_type: str | None = None,
        subject_id: uuid.UUID | None = None,
        *,
        reason: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        audit.record(
            self.session,
            actor_user_id=self.principal.user_id,
            actor_roles=sorted(r.value for r in self.principal.roles),
            action=action,
            subject_type=subject_type,
            subject_id=subject_id,
            reason=reason,
            context=context,
        )


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})


async def load_principal(session: AsyncSession, user_id: uuid.UUID, tenant_id: uuid.UUID, mfa: bool) -> Principal:
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account not active")
    return Principal(
        user_id=user.id,
        tenant_id=tenant_id,
        display_name=user.display_name,
        email=user.email,
        assignments=[Assignment(r.role, r.scope_type, r.scope_id) for r in user.roles],
        mfa=mfa,
    )


async def get_ctx(request: Request) -> AsyncIterator[Ctx]:
    token = _bearer(request)
    try:
        claims = decode_access_token(token)
        user_id = uuid.UUID(claims["sub"])
        tenant_id = uuid.UUID(claims["tid"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from exc

    settings = get_settings()
    if settings.environment != "test" and not await hit(
        f"user:{user_id}", settings.rate_limit_default_per_minute
    ):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Rate limit exceeded")

    async with tenant_session(tenant_id) as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
        if tenant is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown tenant")
        principal = await load_principal(session, user_id, tenant_id, bool(claims.get("mfa")))
        # Admin and leader capabilities require a second factor in this session.
        required = {r for r in principal.roles if r.value in settings.mfa_required_roles}
        if required and not principal.mfa:
            principal.assignments = [a for a in principal.assignments if a.role not in required]
        request.state.principal = principal
        audit.request_meta.set(
            audit.RequestMeta(
                ip=client_ip(request),
                user_agent=request.headers.get("user-agent"),
                request_id=request.headers.get("x-request-id"),
            )
        )
        ctx = Ctx(session=session, principal=principal, tenant=tenant)
        try:
            yield ctx
            if session.in_transaction():
                await session.commit()
        except Exception:
            await session.rollback()
            raise
    for job, kwargs in ctx.deferred:
        await jobs.run_or_enqueue(job, tenant_id, **kwargs)


CtxDep = Annotated[Ctx, Depends(get_ctx, scope="function")]
