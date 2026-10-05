"""Role-based capabilities (spec §2) plus data scoping.

Two independent checks guard every endpoint:

1. Capability — may this kind of user perform this kind of action at all?
2. Scope — is this particular student / squad within reach of this user?

Scope is resolved server-side from role assignments on every request. Out-of-scope
records are reported as 404, so changing an ID in a URL reveals nothing.
"""

import uuid
from dataclasses import dataclass, field
from enum import StrEnum

from fastapi import HTTPException, status
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SquadMembership, StudentGuardian
from app.models.enums import MembershipStatus, Role, ScopeType


class Cap(StrEnum):
    VIEW_ROSTER = "view_roster"
    MANAGE_SQUAD_MEMBERS = "manage_squad_members"
    MANAGE_SQUADS = "manage_squads"
    RECORD_RESULTS = "record_results"
    RECORD_ATTENDANCE = "record_attendance"
    VERIFY_SKILLS = "verify_skills"
    PROPOSE_COMPETITION = "propose_competition"
    MANAGE_COMPETITIONS = "manage_competitions"
    RUN_READINESS = "run_readiness"
    VIEW_SCHOOL_ANALYTICS = "view_school_analytics"
    DRAFT_MESSAGES = "draft_messages"
    RELEASE_MESSAGES = "release_messages"
    APPROVE_TEMPLATES = "approve_templates"
    EMERGENCY_BROADCAST = "emergency_broadcast"
    VIEW_INVENTORY = "view_inventory"
    REQUEST_KIT = "request_kit"
    MANAGE_INVENTORY = "manage_inventory"
    VIEW_BUDGET = "view_budget"
    MANAGE_BUDGET = "manage_budget"
    APPROVE_SPEND = "approve_spend"
    EXPORT_INSPECTION = "export_inspection"
    MANAGE_USERS = "manage_users"
    VIEW_AUDIT_OWN = "view_audit_own"
    VIEW_AUDIT_ALL = "view_audit_all"
    MANAGE_SETTINGS = "manage_settings"
    MANAGE_IMPORTS = "manage_imports"
    MANAGE_CONSENT = "manage_consent"
    UPLOAD_MEDIA = "upload_media"
    REVIEW_INSIGHTS = "review_insights"
    SUBJECT_ACCESS_EXPORT = "subject_access_export"
    MANAGE_WEBHOOKS = "manage_webhooks"
    GENERATE_REPORTS = "generate_reports"
    PORTAL_PARENT = "portal_parent"
    PORTAL_STUDENT = "portal_student"


T, A, L = Role.TEACHER, Role.PROGRAMME_ADMIN, Role.LEADER

CAPABILITIES: dict[Cap, frozenset[Role]] = {
    Cap.VIEW_ROSTER: frozenset({T, A, L}),
    Cap.MANAGE_SQUAD_MEMBERS: frozenset({T, A}),  # teacher: own squads only
    Cap.MANAGE_SQUADS: frozenset({A}),
    Cap.RECORD_RESULTS: frozenset({T, A}),
    Cap.RECORD_ATTENDANCE: frozenset({T, A}),
    Cap.VERIFY_SKILLS: frozenset({T, A}),
    Cap.PROPOSE_COMPETITION: frozenset({T, A}),
    Cap.MANAGE_COMPETITIONS: frozenset({A}),
    Cap.RUN_READINESS: frozenset({T, A, L}),
    Cap.VIEW_SCHOOL_ANALYTICS: frozenset({A, L}),
    Cap.DRAFT_MESSAGES: frozenset({T, A, L}),
    Cap.RELEASE_MESSAGES: frozenset({A, L}),
    Cap.APPROVE_TEMPLATES: frozenset({A, L}),
    Cap.EMERGENCY_BROADCAST: frozenset({L}),
    Cap.VIEW_INVENTORY: frozenset({T, A, L}),
    Cap.REQUEST_KIT: frozenset({T, A}),
    Cap.MANAGE_INVENTORY: frozenset({A}),
    Cap.VIEW_BUDGET: frozenset({A, L}),
    Cap.MANAGE_BUDGET: frozenset({A}),
    Cap.APPROVE_SPEND: frozenset({L}),
    Cap.EXPORT_INSPECTION: frozenset({A, L}),
    Cap.MANAGE_USERS: frozenset({L}),
    Cap.VIEW_AUDIT_OWN: frozenset({A, L}),
    Cap.VIEW_AUDIT_ALL: frozenset({L}),
    Cap.MANAGE_SETTINGS: frozenset({A, L}),
    Cap.MANAGE_IMPORTS: frozenset({A}),
    Cap.MANAGE_CONSENT: frozenset({A}),
    Cap.UPLOAD_MEDIA: frozenset({T, A}),
    Cap.REVIEW_INSIGHTS: frozenset({T, A}),
    Cap.SUBJECT_ACCESS_EXPORT: frozenset({A, L}),
    Cap.MANAGE_WEBHOOKS: frozenset({A, L}),
    Cap.GENERATE_REPORTS: frozenset({T, A, L}),
    Cap.PORTAL_PARENT: frozenset({Role.PARENT}),
    Cap.PORTAL_STUDENT: frozenset({Role.STUDENT}),
}

STAFF_ROLES = frozenset({T, A, L})
WHOLE_SCHOOL_ROLES = frozenset({A, L})


@dataclass(frozen=True)
class Assignment:
    role: Role
    scope_type: ScopeType
    scope_id: uuid.UUID | None


@dataclass
class Principal:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    display_name: str
    email: str
    assignments: list[Assignment]
    mfa: bool = False
    _student_scope: "frozenset[uuid.UUID] | None" = field(default=None, repr=False)
    _student_scope_loaded: bool = field(default=False, repr=False)

    @property
    def roles(self) -> set[Role]:
        return {a.role for a in self.assignments}

    @property
    def is_staff(self) -> bool:
        return bool(self.roles & STAFF_ROLES)

    @property
    def whole_school(self) -> bool:
        return any(a.role in WHOLE_SCHOOL_ROLES for a in self.assignments)

    def can(self, cap: Cap) -> bool:
        return bool(self.roles & CAPABILITIES[cap])

    def require(self, cap: Cap) -> None:
        if not self.can(cap):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Not permitted: {cap.value}")

    def scope_ids(self, role: Role, scope_type: ScopeType) -> set[uuid.UUID]:
        return {
            a.scope_id
            for a in self.assignments
            if a.role == role and a.scope_type == scope_type and a.scope_id is not None
        }

    @property
    def coached_squad_ids(self) -> set[uuid.UUID]:
        return self.scope_ids(Role.TEACHER, ScopeType.SQUAD)

    @property
    def guardian_ids(self) -> set[uuid.UUID]:
        return self.scope_ids(Role.PARENT, ScopeType.GUARDIAN)

    @property
    def own_student_ids(self) -> set[uuid.UUID]:
        return self.scope_ids(Role.STUDENT, ScopeType.STUDENT)


ALL = None  # sentinel: unrestricted within the tenant


async def staff_student_scope(session: AsyncSession, p: Principal) -> frozenset[uuid.UUID] | None:
    """Students a member of staff may see. None means the whole school."""
    if p.whole_school:
        return ALL
    if p._student_scope_loaded:
        return p._student_scope
    ids: set[uuid.UUID] = set()
    if p.coached_squad_ids:
        rows = await session.scalars(
            select(SquadMembership.student_id).where(
                SquadMembership.squad_id.in_(p.coached_squad_ids),
                SquadMembership.status == MembershipStatus.ACTIVE,
            )
        )
        ids.update(rows)
    p._student_scope = frozenset(ids)
    p._student_scope_loaded = True
    return p._student_scope


async def family_student_ids(session: AsyncSession, p: Principal) -> set[uuid.UUID]:
    """Students a parent or student account may see in the portal."""
    ids = set(p.own_student_ids)
    if p.guardian_ids:
        rows = await session.scalars(
            select(StudentGuardian.student_id).where(StudentGuardian.guardian_id.in_(p.guardian_ids))
        )
        ids.update(rows)
    return ids


def not_found(what: str = "Not found") -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, what)


async def ensure_student_in_scope(session: AsyncSession, p: Principal, student_id: uuid.UUID) -> None:
    if not p.is_staff:
        raise not_found("Student not found")
    scope = await staff_student_scope(session, p)
    if scope is not ALL and student_id not in scope:
        raise not_found("Student not found")


def ensure_squad_in_scope(p: Principal, squad_id: uuid.UUID) -> None:
    if p.whole_school:
        return
    if squad_id not in p.coached_squad_ids:
        raise not_found("Squad not found")


def ensure_can_write_squad(p: Principal, squad_id: uuid.UUID, cap: Cap) -> None:
    """Teacher write capabilities apply to their own squads only; admins to all."""
    p.require(cap)
    if Role.PROGRAMME_ADMIN in p.roles:
        return
    if squad_id not in p.coached_squad_ids:
        raise not_found("Squad not found")


def apply_student_scope(stmt: Select, column, scope: frozenset[uuid.UUID] | None) -> Select:  # noqa: ANN001
    if scope is ALL:
        return stmt
    return stmt.where(column.in_(scope or {uuid.UUID(int=0)}))
