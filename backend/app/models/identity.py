import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantScoped, enum_column, utcnow
from app.models.enums import Language, Role, ScopeType


class Tenant(Base):
    """One school. A multi-campus group is several tenants (spec §10.1)."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    name_ar: Mapped[str | None] = mapped_column(String(200))
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)


class User(TenantScoped, Base):
    """Anyone who can sign in: staff, parents and (optionally) students."""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "email"),)

    email: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    oidc_subject: Mapped[str | None] = mapped_column(String(255), index=True)
    locale: Mapped[Language] = enum_column(Language, default=Language.EN)
    mfa_secret_enc: Mapped[str | None] = mapped_column(String(512))
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None]

    roles: Mapped[list["RoleAssignment"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
        foreign_keys="RoleAssignment.user_id",
    )


class RoleAssignment(TenantScoped, Base):
    """Many-to-many role assignment with a scope — never a single role column on the user.

    teacher/school   — a member of staff with no student access by default
    teacher/squad    — coaches that squad; sees only its students
    parent/guardian  — bound to one guardian record; sees only that guardian's children
    student/student  — bound to one student record; sees only themself
    programme_admin/school, leader/school — whole school
    """

    __tablename__ = "role_assignments"
    __table_args__ = (UniqueConstraint("user_id", "role", "scope_type", "scope_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[Role] = enum_column(Role)
    scope_type: Mapped[ScopeType] = enum_column(ScopeType, default=ScopeType.SCHOOL)
    scope_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    granted_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    user: Mapped[User] = relationship(back_populates="roles", foreign_keys=[user_id])


class RefreshToken(TenantScoped, Base):
    """Opaque refresh tokens with rotation. Re-use of a rotated token revokes the family."""

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    family_id: Mapped[uuid.UUID] = mapped_column(index=True)
    mfa_satisfied: Mapped[bool] = mapped_column(Boolean, default=False)
    last_used_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    absolute_expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    replaced_by_id: Mapped[uuid.UUID | None]
    user_agent: Mapped[str | None] = mapped_column(String(400))


class MagicLink(TenantScoped, Base):
    __tablename__ = "magic_links"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
    requested_ip: Mapped[str | None] = mapped_column(String(64))


class AuditEvent(TenantScoped, Base):
    """Append-only. The runtime role has INSERT/SELECT only; purge goes through a
    SECURITY DEFINER function so retention cannot be used to rewrite history."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_subject", "subject_type", "subject_id"),
        Index("ix_audit_actor_at", "actor_user_id", "created_at"),
    )

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    actor_roles: Mapped[list[str]] = mapped_column(default=list)
    action: Mapped[str] = mapped_column(String(80), index=True)
    subject_type: Mapped[str | None] = mapped_column(String(40))
    subject_id: Mapped[uuid.UUID | None]
    reason: Mapped[str | None] = mapped_column(String(1000))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(400))
    request_id: Mapped[str | None] = mapped_column(String(64))
    context: Mapped[dict[str, Any]] = mapped_column(default=dict)
