import secrets
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, select

from app.config import get_settings
from app.deps import CtxDep
from app.models import (
    CompetitionEdition,
    RoleAssignment,
    Squad,
    SquadMembership,
    SquadTargetEdition,
    Student,
    User,
)
from app.models.enums import MembershipStatus, Role, ScopeType
from app.permissions import Cap, ensure_can_write_squad, ensure_squad_in_scope, not_found
from app.schemas.domain import MembershipIn, MembershipOut, MembershipPatch, SquadIn, SquadOut
from app.services.messaging.auto import queue_selection_notice
from app.services.recommendations import squad_readiness

router = APIRouter(prefix="/squads", tags=["squads"])


async def _squad(ctx, squad_id: uuid.UUID) -> Squad:  # noqa: ANN001
    ctx.require(Cap.VIEW_ROSTER)
    ensure_squad_in_scope(ctx.principal, squad_id)
    squad = await ctx.session.get(Squad, squad_id)
    if squad is None:
        raise not_found("Squad not found")
    return squad


@router.get("", response_model=list[SquadOut])
async def list_squads(ctx: CtxDep, include_inactive: bool = False) -> list[SquadOut]:
    ctx.require(Cap.VIEW_ROSTER)
    stmt = select(Squad).order_by(Squad.name)
    if not ctx.principal.whole_school:
        stmt = stmt.where(Squad.id.in_(ctx.principal.coached_squad_ids or {uuid.UUID(int=0)}))
    if not include_inactive:
        stmt = stmt.where(Squad.is_active.is_(True))
    return [SquadOut.model_validate(s) for s in (await ctx.session.scalars(stmt)).all()]


@router.post("", response_model=SquadOut, status_code=201)
async def create_squad(body: SquadIn, ctx: CtxDep) -> SquadOut:
    ctx.require(Cap.MANAGE_SQUADS)
    squad = Squad(**body.model_dump())
    ctx.session.add(squad)
    await ctx.session.flush()
    if body.lead_coach_user_id:
        await _assign_coach(ctx, squad.id, body.lead_coach_user_id)
    ctx.audit("squad.create", "squad", squad.id)
    return SquadOut.model_validate(squad)


@router.patch("/{squad_id}", response_model=SquadOut)
async def update_squad(squad_id: uuid.UUID, body: SquadIn, ctx: CtxDep) -> SquadOut:
    ctx.require(Cap.MANAGE_SQUADS)
    squad = await _squad(ctx, squad_id)
    for k, v in body.model_dump().items():
        setattr(squad, k, v)
    ctx.audit("squad.update", "squad", squad.id)
    return SquadOut.model_validate(squad)


@router.get("/{squad_id}")
async def get_squad(squad_id: uuid.UUID, ctx: CtxDep) -> dict:
    squad = await _squad(ctx, squad_id)
    rows = (
        await ctx.session.execute(
            select(SquadMembership, Student)
            .join(Student, Student.id == SquadMembership.student_id)
            .where(SquadMembership.squad_id == squad.id)
            .order_by(SquadMembership.status, Student.family_name)
        )
    ).all()
    coaches = (
        await ctx.session.execute(
            select(User.id, User.display_name)
            .join(RoleAssignment, RoleAssignment.user_id == User.id)
            .where(
                RoleAssignment.role == Role.TEACHER,
                RoleAssignment.scope_type == ScopeType.SQUAD,
                RoleAssignment.scope_id == squad.id,
            )
        )
    ).all()
    editions = (
        await ctx.session.scalars(
            select(CompetitionEdition)
            .join(SquadTargetEdition, SquadTargetEdition.edition_id == CompetitionEdition.id)
            .where(SquadTargetEdition.squad_id == squad.id)
            .order_by(CompetitionEdition.event_starts)
        )
    ).all()
    ctx.audit("squad.roster_read", "squad", squad.id, context={"student_ids": [s.id for _, s in rows]})
    return {
        **SquadOut.model_validate(squad).model_dump(),
        "members": [
            {
                **MembershipOut.model_validate(m).model_dump(),
                "name": s.display_name,
                "year_group": s.year_group,
            }
            for m, s in rows
        ],
        "coaches": [
            {"user_id": uid, "name": name, "is_lead": uid == squad.lead_coach_user_id}
            for uid, name in coaches
        ],
        "target_editions": [
            {
                "edition_id": e.id,
                "name": e.name,
                "competition_name": e.competition.name,
                "event_starts": e.event_starts,
                "event_ends": e.event_ends,
                "tier": e.tier.value,
            }
            for e in editions
        ],
    }


@router.post("/{squad_id}/members", response_model=MembershipOut, status_code=201)
async def add_member(squad_id: uuid.UUID, body: MembershipIn, ctx: CtxDep) -> MembershipOut:
    ensure_can_write_squad(ctx.principal, squad_id, Cap.MANAGE_SQUAD_MEMBERS)
    squad = await ctx.session.get(Squad, squad_id)
    student = await ctx.session.get(Student, body.student_id)
    if squad is None or student is None:
        raise not_found()
    m = await ctx.session.scalar(
        select(SquadMembership).where(
            SquadMembership.squad_id == squad_id, SquadMembership.student_id == student.id
        )
    )
    newly_selected = m is None or m.status == MembershipStatus.WITHDRAWN
    if m is None:
        m = SquadMembership(squad_id=squad_id, student_id=student.id)
        ctx.session.add(m)
    m.role, m.is_reserve, m.status = body.role, body.is_reserve, MembershipStatus.ACTIVE
    m.joined_on = body.joined_on or datetime.now(UTC).date()
    m.withdrawn_on, m.withdrawal_reason = None, None
    await ctx.session.flush()
    if newly_selected:
        await queue_selection_notice(ctx.session, squad, student, ctx.user_id)
    ctx.audit("squad.member_add", "student", student.id, context={"squad_id": squad_id, "role": body.role})
    return MembershipOut.model_validate(m)


@router.patch("/{squad_id}/members/{membership_id}", response_model=MembershipOut)
async def update_member(
    squad_id: uuid.UUID, membership_id: uuid.UUID, body: MembershipPatch, ctx: CtxDep
) -> MembershipOut:
    ensure_can_write_squad(ctx.principal, squad_id, Cap.MANAGE_SQUAD_MEMBERS)
    m = await ctx.session.get(SquadMembership, membership_id)
    if m is None or m.squad_id != squad_id:
        raise not_found()
    if body.role is not None:
        m.role = body.role
    if body.is_reserve is not None:
        m.is_reserve = body.is_reserve
    if body.withdraw:
        if not body.withdrawal_reason:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A withdrawal reason is required")
        m.status = MembershipStatus.WITHDRAWN
        m.withdrawn_on = datetime.now(UTC).date()
        m.withdrawal_reason = body.withdrawal_reason
    ctx.audit(
        "squad.member_update", "student", m.student_id, context={"squad_id": squad_id, **body.model_dump()}
    )
    return MembershipOut.model_validate(m)


class CoachIn(BaseModel):
    user_id: uuid.UUID
    is_lead: bool = False


async def _assign_coach(ctx, squad_id: uuid.UUID, user_id: uuid.UUID) -> None:  # noqa: ANN001
    user = await ctx.session.get(User, user_id)
    if user is None:
        raise not_found("User not found")
    exists = await ctx.session.scalar(
        select(RoleAssignment).where(
            RoleAssignment.user_id == user_id,
            RoleAssignment.role == Role.TEACHER,
            RoleAssignment.scope_type == ScopeType.SQUAD,
            RoleAssignment.scope_id == squad_id,
        )
    )
    if exists is None:
        ctx.session.add(
            RoleAssignment(
                user_id=user_id,
                role=Role.TEACHER,
                scope_type=ScopeType.SQUAD,
                scope_id=squad_id,
                granted_by_id=ctx.user_id,
            )
        )


@router.post("/{squad_id}/coaches", status_code=201)
async def add_coach(squad_id: uuid.UUID, body: CoachIn, ctx: CtxDep) -> dict:
    ctx.require(Cap.MANAGE_SQUADS)
    squad = await _squad(ctx, squad_id)
    await _assign_coach(ctx, squad_id, body.user_id)
    if body.is_lead:
        squad.lead_coach_user_id = body.user_id
    ctx.audit("squad.coach_add", "squad", squad_id, context={"user_id": body.user_id})
    return {"ok": True}


@router.delete("/{squad_id}/coaches/{user_id}", status_code=204)
async def remove_coach(squad_id: uuid.UUID, user_id: uuid.UUID, ctx: CtxDep) -> None:
    ctx.require(Cap.MANAGE_SQUADS)
    await ctx.session.execute(
        delete(RoleAssignment).where(
            RoleAssignment.user_id == user_id,
            RoleAssignment.role == Role.TEACHER,
            RoleAssignment.scope_type == ScopeType.SQUAD,
            RoleAssignment.scope_id == squad_id,
        )
    )
    ctx.audit("squad.coach_remove", "squad", squad_id, context={"user_id": user_id})


class TargetIn(BaseModel):
    edition_id: uuid.UUID


@router.post("/{squad_id}/editions", status_code=201)
async def add_target_edition(squad_id: uuid.UUID, body: TargetIn, ctx: CtxDep) -> dict:
    ctx.require(Cap.MANAGE_SQUADS)
    await _squad(ctx, squad_id)
    if await ctx.session.get(CompetitionEdition, body.edition_id) is None:
        raise not_found("Edition not found")
    exists = await ctx.session.scalar(
        select(SquadTargetEdition).where(
            SquadTargetEdition.squad_id == squad_id, SquadTargetEdition.edition_id == body.edition_id
        )
    )
    if exists is None:
        ctx.session.add(SquadTargetEdition(squad_id=squad_id, edition_id=body.edition_id))
    ctx.audit("squad.edition_target", "squad", squad_id, context={"edition_id": body.edition_id})
    return {"ok": True}


@router.delete("/{squad_id}/editions/{edition_id}", status_code=204)
async def remove_target_edition(squad_id: uuid.UUID, edition_id: uuid.UUID, ctx: CtxDep) -> None:
    ctx.require(Cap.MANAGE_SQUADS)
    await ctx.session.execute(
        delete(SquadTargetEdition).where(
            SquadTargetEdition.squad_id == squad_id, SquadTargetEdition.edition_id == edition_id
        )
    )
    ctx.audit("squad.edition_untarget", "squad", squad_id, context={"edition_id": edition_id})


@router.get("/{squad_id}/readiness")
async def get_squad_readiness(squad_id: uuid.UUID, ctx: CtxDep, edition_id: uuid.UUID | None = None) -> dict:
    """Readiness per student, the shared gap list, and a suggested focus for next session."""
    ctx.require(Cap.RUN_READINESS)
    squad = await _squad(ctx, squad_id)
    out = await squad_readiness(ctx.session, ctx.settings, squad, datetime.now(UTC).date(), edition_id)
    ctx.audit("squad.readiness_read", "squad", squad.id)
    return out


@router.post("/{squad_id}/calendar-link")
async def calendar_link(squad_id: uuid.UUID, ctx: CtxDep) -> dict:
    """Read-only ICS feed for the squad's events and training (no student data)."""
    squad = await _squad(ctx, squad_id)
    if not squad.ics_token:
        squad.ics_token = f"{ctx.tenant.id.hex}.{secrets.token_urlsafe(24)}"
    return {"url": f"{get_settings().api_base_url}/api/v1/calendar/{squad.ics_token}.ics"}
