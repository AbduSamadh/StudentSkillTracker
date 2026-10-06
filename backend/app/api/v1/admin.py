import io
import json
import secrets
import uuid
import zipfile
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import delete, select

from app.audit import _jsonable
from app.deps import CtxDep
from app.models import Base, RoleAssignment, User, WebhookSubscription
from app.models.enums import Language, Role, ScopeType, WebhookEvent
from app.permissions import Cap, not_found
from app.schemas.types import Email
from app.security import encrypt_field, hash_password
from app.tenancy import TenantSettings, load_settings

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------- Users and roles (Leader only) ----------------
class RoleIn(BaseModel):
    role: Role
    scope_type: ScopeType = ScopeType.SCHOOL
    scope_id: uuid.UUID | None = None


class UserIn(BaseModel):
    email: Email
    display_name: str
    locale: Language = Language.EN
    roles: list[RoleIn] = []
    initial_password: str | None = Field(default=None, min_length=12)


def _user_out(u: User) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "display_name": u.display_name,
        "locale": u.locale.value,
        "is_active": u.is_active,
        "mfa_enabled": u.mfa_enabled,
        "last_login_at": u.last_login_at,
        "sso_linked": bool(u.oidc_subject),
        "roles": [
            {"id": r.id, "role": r.role.value, "scope_type": r.scope_type.value, "scope_id": r.scope_id}
            for r in u.roles
        ],
    }


def _validate_role(r: RoleIn) -> None:
    allowed = {
        Role.TEACHER: {ScopeType.SCHOOL, ScopeType.SQUAD},
        Role.PROGRAMME_ADMIN: {ScopeType.SCHOOL},
        Role.LEADER: {ScopeType.SCHOOL},
        Role.PARENT: {ScopeType.GUARDIAN},
        Role.STUDENT: {ScopeType.STUDENT},
    }
    if r.scope_type not in allowed[r.role]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"{r.role.value} cannot be scoped to {r.scope_type.value}"
        )
    if r.scope_type != ScopeType.SCHOOL and r.scope_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "scope_id is required")


@router.get("/users")
async def list_users(ctx: CtxDep, role: Role | None = None) -> list[dict]:
    ctx.require(Cap.MANAGE_USERS)
    stmt = select(User).order_by(User.display_name)
    if role:
        stmt = stmt.where(User.id.in_(select(RoleAssignment.user_id).where(RoleAssignment.role == role)))
    return [_user_out(u) for u in (await ctx.session.scalars(stmt)).all()]


@router.get("/staff")
async def list_staff(ctx: CtxDep) -> list[dict]:
    """Staff directory for coach assignment (admins need it too)."""
    ctx.require(Cap.MANAGE_SQUADS)
    stmt = (
        select(User)
        .where(
            User.id.in_(
                select(RoleAssignment.user_id).where(
                    RoleAssignment.role.in_([Role.TEACHER, Role.PROGRAMME_ADMIN, Role.LEADER])
                )
            )
        )
        .order_by(User.display_name)
    )
    return [
        {"id": u.id, "display_name": u.display_name, "email": u.email}
        for u in (await ctx.session.scalars(stmt)).all()
    ]


@router.get("/directory")
async def staff_directory(ctx: CtxDep) -> list[dict]:
    """Names of staff, so evidence can say who verified it. IDs and display names only."""
    ctx.require(Cap.VIEW_ROSTER)
    stmt = select(User.id, User.display_name).where(
        User.id.in_(
            select(RoleAssignment.user_id).where(
                RoleAssignment.role.in_([Role.TEACHER, Role.PROGRAMME_ADMIN, Role.LEADER])
            )
        )
    )
    return [{"id": i, "display_name": n} for i, n in (await ctx.session.execute(stmt)).all()]


@router.post("/users", status_code=201)
async def create_user(body: UserIn, ctx: CtxDep) -> dict:
    ctx.require(Cap.MANAGE_USERS)
    if await ctx.session.scalar(select(User).where(User.email == body.email.lower())):
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with this email exists")
    u = User(
        email=body.email.lower(),
        display_name=body.display_name,
        locale=body.locale,
        password_hash=hash_password(body.initial_password) if body.initial_password else None,
    )
    ctx.session.add(u)
    await ctx.session.flush()
    for r in body.roles:
        _validate_role(r)
        ctx.session.add(
            RoleAssignment(
                user_id=u.id,
                role=r.role,
                scope_type=r.scope_type,
                scope_id=r.scope_id,
                granted_by_id=ctx.user_id,
            )
        )
    await ctx.session.flush()
    await ctx.session.refresh(u, ["roles"])
    ctx.audit("user.create", "user", u.id, context={"roles": [r.model_dump(mode="json") for r in body.roles]})
    return _user_out(u)


@router.post("/users/{user_id}/roles", status_code=201)
async def grant_role(user_id: uuid.UUID, body: RoleIn, ctx: CtxDep) -> dict:
    ctx.require(Cap.MANAGE_USERS)
    u = await ctx.session.get(User, user_id)
    if u is None:
        raise not_found()
    _validate_role(body)
    exists = await ctx.session.scalar(
        select(RoleAssignment).where(
            RoleAssignment.user_id == u.id,
            RoleAssignment.role == body.role,
            RoleAssignment.scope_type == body.scope_type,
            RoleAssignment.scope_id == body.scope_id if body.scope_id else RoleAssignment.scope_id.is_(None),
        )
    )
    if exists is None:
        ctx.session.add(
            RoleAssignment(
                user_id=u.id,
                role=body.role,
                scope_type=body.scope_type,
                scope_id=body.scope_id,
                granted_by_id=ctx.user_id,
            )
        )
    await ctx.session.flush()
    await ctx.session.refresh(u, ["roles"])
    ctx.audit("user.role_grant", "user", u.id, context=body.model_dump(mode="json"))
    return _user_out(u)


@router.delete("/users/{user_id}/roles/{assignment_id}", status_code=204)
async def revoke_role(user_id: uuid.UUID, assignment_id: uuid.UUID, ctx: CtxDep) -> None:
    ctx.require(Cap.MANAGE_USERS)
    if user_id == ctx.user_id:
        a = await ctx.session.get(RoleAssignment, assignment_id)
        if a and a.role == Role.LEADER:
            raise HTTPException(status.HTTP_409_CONFLICT, "You cannot remove your own leader role")
    await ctx.session.execute(
        delete(RoleAssignment).where(RoleAssignment.id == assignment_id, RoleAssignment.user_id == user_id)
    )
    ctx.audit("user.role_revoke", "user", user_id, context={"assignment_id": assignment_id})


class ActiveIn(BaseModel):
    is_active: bool


@router.post("/users/{user_id}/active")
async def set_active(user_id: uuid.UUID, body: ActiveIn, ctx: CtxDep) -> dict:
    ctx.require(Cap.MANAGE_USERS)
    u = await ctx.session.get(User, user_id)
    if u is None:
        raise not_found()
    if u.id == ctx.user_id and not body.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, "You cannot deactivate yourself")
    u.is_active = body.is_active
    ctx.audit("user.active", "user", u.id, context={"is_active": body.is_active})
    return _user_out(u)


@router.post("/users/{user_id}/reset-mfa")
async def reset_mfa(user_id: uuid.UUID, ctx: CtxDep) -> dict:
    ctx.require(Cap.MANAGE_USERS)
    u = await ctx.session.get(User, user_id)
    if u is None:
        raise not_found()
    u.mfa_enabled, u.mfa_secret_enc = False, None
    ctx.audit("user.mfa_reset", "user", u.id)
    return _user_out(u)


# ---------------- Settings ----------------
@router.get("/settings")
async def get_settings_(ctx: CtxDep) -> dict:
    ctx.require(Cap.MANAGE_SETTINGS)
    s = ctx.settings.model_dump(mode="json")
    s["oidc"].pop("client_secret_enc", None)
    return {"name": ctx.tenant.name, "name_ar": ctx.tenant.name_ar, "settings": s}


class SettingsIn(BaseModel):
    settings: dict
    oidc_client_secret: str | None = None


@router.put("/settings")
async def put_settings(body: SettingsIn, ctx: CtxDep) -> dict:
    ctx.require(Cap.MANAGE_SETTINGS)
    merged = {**ctx.tenant.settings, **body.settings}
    if "oidc" in body.settings and Role.LEADER not in ctx.principal.roles:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only leaders change SSO configuration")
    if body.oidc_client_secret:
        merged.setdefault("oidc", {})["client_secret_enc"] = encrypt_field(body.oidc_client_secret)
    elif "oidc" in body.settings:
        merged["oidc"]["client_secret_enc"] = (ctx.tenant.settings.get("oidc") or {}).get("client_secret_enc")
    try:
        validated = TenantSettings.model_validate(merged)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    before = load_settings(ctx.tenant.settings).model_dump(mode="json")
    ctx.tenant.settings = validated.model_dump(mode="json")
    changed = sorted(k for k in body.settings if before.get(k) != ctx.tenant.settings.get(k))
    ctx.audit("settings.update", "tenant", ctx.tenant.id, context={"changed": changed})
    return await get_settings_(ctx)


# ---------------- Webhook subscriptions ----------------
class WebhookIn(BaseModel):
    url: HttpUrl
    events: list[WebhookEvent]


@router.get("/webhooks")
async def list_webhooks(ctx: CtxDep) -> list[dict]:
    ctx.require(Cap.MANAGE_WEBHOOKS)
    rows = (await ctx.session.scalars(select(WebhookSubscription))).all()
    return [{"id": w.id, "url": w.url, "events": w.events, "is_active": w.is_active} for w in rows]


@router.post("/webhooks", status_code=201)
async def create_webhook(body: WebhookIn, ctx: CtxDep) -> dict:
    """The signing secret is shown once, at creation."""
    ctx.require(Cap.MANAGE_WEBHOOKS)
    if not str(body.url).startswith("https://"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Webhook URLs must use HTTPS")
    secret = secrets.token_hex(32)
    w = WebhookSubscription(
        url=str(body.url),
        events=[e.value for e in body.events],
        secret_enc=encrypt_field(secret) or "",
        created_by_id=ctx.user_id,
    )
    ctx.session.add(w)
    await ctx.session.flush()
    ctx.audit("webhook.create", "webhook", w.id, context={"url": str(body.url), "events": w.events})
    return {"id": w.id, "url": w.url, "events": w.events, "secret": secret}


@router.delete("/webhooks/{webhook_id}", status_code=204)
async def delete_webhook(webhook_id: uuid.UUID, ctx: CtxDep) -> None:
    ctx.require(Cap.MANAGE_WEBHOOKS)
    w = await ctx.session.get(WebhookSubscription, webhook_id)
    if w is None:
        raise not_found()
    await ctx.session.delete(w)
    ctx.audit("webhook.delete", "webhook", webhook_id)


# ---------------- Full-data export (no lock-in) ----------------
SKIP_EXPORT = {"refresh_tokens", "magic_links"}
SECRET_COLUMNS = {"password_hash", "mfa_secret_enc", "secret_enc", "token_hash", "open_token", "ics_token"}


@router.get("/export")
async def full_export(ctx: CtxDep, reason: str) -> Response:
    """Every tenant table as JSON in one ZIP. Credentials are excluded; encrypted contact
    fields stay encrypted. Leaders only, always logged with a reason."""
    ctx.require(Cap.MANAGE_USERS)
    buf = io.BytesIO()
    counts = {}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for table in Base.metadata.sorted_tables:
            if table.name in SKIP_EXPORT or table.name == "tenants":
                continue
            cols = [c for c in table.columns if c.name not in SECRET_COLUMNS]
            rows = (await ctx.session.execute(select(*cols))).mappings().all()
            counts[table.name] = len(rows)
            z.writestr(
                f"{table.name}.json",
                json.dumps(_jsonable([dict(r) for r in rows]), ensure_ascii=False, indent=1),
            )
        z.writestr(
            "README.txt",
            "Full data export. Contact details are field-encrypted; decrypt with the "
            "school's field encryption key. One JSON file per table.\n",
        )
    ctx.audit("tenant.full_export", "tenant", ctx.tenant.id, reason=reason, context=counts)
    name = f"{ctx.tenant.slug}-export-{datetime.now(UTC):%Y%m%d}.zip"
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
