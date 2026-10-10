"""What is waiting for me: one request behind each member of staff's home page.

Each count uses the same capability and scope rules as the page it links to, so the number on
the home page matches what the person finds when they open that page. Counts are only returned
for the things the caller can act on.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter
from sqlalchemy import Select, func, select

from app.deps import CtxDep
from app.models import (
    AssetRequest,
    BudgetLine,
    Competition,
    CompetitionEdition,
    Insight,
    Message,
    MessageTemplate,
    Season,
    SkillAward,
    Squad,
    SquadMembership,
    SquadTargetEdition,
    StudentFlag,
)
from app.models.enums import (
    ApprovalStatus,
    AssetRequestStatus,
    AwardStatus,
    CompetitionStatus,
    InsightStatus,
    MembershipStatus,
    MessageStatus,
    TemplateStatus,
)
from app.permissions import Cap, apply_student_scope, staff_student_scope

router = APIRouter(tags=["home"])


async def _count(ctx, stmt: Select) -> int:  # noqa: ANN001
    return int(await ctx.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)


@router.get("/home")
async def home(ctx: CtxDep) -> dict:
    ctx.require(Cap.VIEW_ROSTER)
    p = ctx.principal
    scope = await staff_student_scope(ctx.session, p)
    counts: dict[str, int] = {}

    if p.can(Cap.VERIFY_SKILLS):
        counts["skills_to_approve"] = await _count(
            ctx,
            apply_student_scope(select(SkillAward.id), SkillAward.student_id, scope).where(
                SkillAward.status == AwardStatus.PROPOSED
            ),
        )
    if p.can(Cap.RUN_READINESS):
        counts["students_to_check"] = await _count(
            ctx,
            apply_student_scope(
                select(StudentFlag.student_id).distinct(), StudentFlag.student_id, scope
            ).where(StudentFlag.resolved_at.is_(None)),
        )
    if p.can(Cap.REVIEW_INSIGHTS):
        counts["notes_to_review"] = await _count(
            ctx,
            apply_student_scope(select(Insight.id), Insight.student_id, scope).where(
                Insight.status == InsightStatus.DRAFT
            ),
        )
    if p.can(Cap.RELEASE_MESSAGES):
        counts["messages_to_release"] = await _count(
            ctx, select(Message.id).where(Message.status == MessageStatus.DRAFT)
        )
    elif p.can(Cap.DRAFT_MESSAGES):
        counts["drafts_awaiting_release"] = await _count(
            ctx,
            select(Message.id).where(
                Message.status == MessageStatus.DRAFT, Message.created_by_id == ctx.user_id
            ),
        )
    if p.can(Cap.APPROVE_TEMPLATES):
        counts["templates_to_approve"] = await _count(
            ctx, select(MessageTemplate.id).where(MessageTemplate.status == TemplateStatus.DRAFT)
        )
    if p.can(Cap.APPROVE_SPEND):
        # The budget page shows the latest season, so count the same lines.
        season_id = await ctx.session.scalar(select(Season.id).order_by(Season.starts_on.desc()).limit(1))
        stmt = select(BudgetLine.id).where(BudgetLine.status == ApprovalStatus.PROPOSED)
        if season_id:
            stmt = stmt.where(BudgetLine.season_id == season_id)
        counts["spend_to_approve"] = await _count(ctx, stmt)
    if p.can(Cap.MANAGE_INVENTORY):
        counts["kit_requests"] = await _count(
            ctx, select(AssetRequest.id).where(AssetRequest.status == AssetRequestStatus.OPEN)
        )
    if p.can(Cap.MANAGE_COMPETITIONS):
        counts["competitions_to_approve"] = await _count(
            ctx, select(Competition.id).where(Competition.status == CompetitionStatus.PROPOSED)
        )

    return {"counts": counts, "squads": await _squads(ctx)}


async def _squads(ctx) -> list[dict]:  # noqa: ANN001
    """The caller's active squads, with how many students are in each and what comes next."""
    p = ctx.principal
    stmt = select(Squad).where(Squad.is_active.is_(True)).order_by(Squad.name)
    if not p.whole_school:
        stmt = stmt.where(Squad.id.in_(p.coached_squad_ids or {uuid.UUID(int=0)}))
    squads = list((await ctx.session.scalars(stmt)).all())
    if not squads:
        return []
    ids = [s.id for s in squads]
    members = dict(
        (
            await ctx.session.execute(
                select(SquadMembership.squad_id, func.count())
                .where(SquadMembership.squad_id.in_(ids), SquadMembership.status == MembershipStatus.ACTIVE)
                .group_by(SquadMembership.squad_id)
            )
        ).all()
    )
    today = datetime.now(UTC).date()
    upcoming = (
        await ctx.session.execute(
            select(SquadTargetEdition.squad_id, CompetitionEdition)
            .join(CompetitionEdition, CompetitionEdition.id == SquadTargetEdition.edition_id)
            .where(SquadTargetEdition.squad_id.in_(ids), CompetitionEdition.event_ends >= today)
            .order_by(CompetitionEdition.event_starts)
        )
    ).all()
    next_event: dict[uuid.UUID, CompetitionEdition] = {}
    for squad_id, edition in upcoming:
        next_event.setdefault(squad_id, edition)
    return [
        {
            "id": s.id,
            "name": s.name,
            "discipline": s.discipline,
            "member_count": members.get(s.id, 0),
            "next_event": {
                "edition_id": e.id,
                "name": e.name,
                "starts": e.event_starts,
                "tier": e.tier.value,
            }
            if (e := next_event.get(s.id))
            else None,
        }
        for s in squads
    ]
