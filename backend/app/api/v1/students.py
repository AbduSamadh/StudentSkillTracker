import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, or_, select

from app.deps import CtxDep
from app.models import (
    Attendance,
    CompetitionEdition,
    Guardian,
    Result,
    ResultParticipant,
    SkillAward,
    SkillGoal,
    Squad,
    SquadMembership,
    Student,
    StudentFlag,
)
from app.models.enums import AttendanceStatus, AwardStatus, FlagKind, MembershipStatus
from app.models.skills import LEVELS
from app.permissions import Cap, apply_student_scope, ensure_student_in_scope, not_found, staff_student_scope
from app.schemas.common import Page
from app.schemas.domain import GoalIn, GoalOut, StudentOut
from app.security import decrypt_field
from app.services.awards import award_out
from app.services.readiness import readiness_for
from app.services.recommendations import competition_recommendations

router = APIRouter(prefix="/students", tags=["students"])


async def _get_student(ctx, student_id: uuid.UUID) -> Student:  # noqa: ANN001
    await ensure_student_in_scope(ctx.session, ctx.principal, student_id)
    student = await ctx.session.get(Student, student_id)
    if student is None:
        raise not_found("Student not found")
    return student


@router.get("", response_model=Page[StudentOut])
async def list_students(
    ctx: CtxDep,
    year_group: int | None = None,
    squad_id: uuid.UUID | None = None,
    flag: FlagKind | None = None,
    q: str | None = Query(default=None, max_length=80),
    include_left: bool = False,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
) -> Page[StudentOut]:
    ctx.require(Cap.VIEW_ROSTER)
    scope = await staff_student_scope(ctx.session, ctx.principal)
    stmt = apply_student_scope(select(Student), Student.id, scope)
    if not include_left:
        stmt = stmt.where(Student.enrolment_status == "active")
    if year_group is not None:
        stmt = stmt.where(Student.year_group == year_group)
    if squad_id is not None:
        stmt = stmt.where(
            Student.id.in_(
                select(SquadMembership.student_id).where(
                    SquadMembership.squad_id == squad_id, SquadMembership.status == MembershipStatus.ACTIVE
                )
            )
        )
    if flag is not None:
        stmt = stmt.where(
            Student.id.in_(
                select(StudentFlag.student_id).where(
                    StudentFlag.kind == flag, StudentFlag.resolved_at.is_(None)
                )
            )
        )
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Student.given_name).like(like),
                func.lower(Student.family_name).like(like),
                func.lower(func.coalesce(Student.preferred_name, "")).like(like),
                Student.external_mis_id == q,
            )
        )
    total = await ctx.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        await ctx.session.scalars(
            stmt.order_by(Student.year_group, Student.family_name).limit(limit).offset(offset)
        )
    ).all()
    ctx.audit(
        "student.list", "student", None, context={"count": len(rows), "student_ids": [s.id for s in rows]}
    )
    return Page(items=[StudentOut.model_validate(s) for s in rows], total=total, limit=limit, offset=offset)


class GuardianSummary(BaseModel):
    id: uuid.UUID
    full_name: str
    relationship: str
    preferred_channel: str
    language: str
    email: str | None = None
    phone: str | None = None
    whatsapp: str | None = None


class StudentDetail(StudentOut):
    guardians: list[GuardianSummary]
    squads: list[dict]
    open_flags: list[dict]


@router.get("/{student_id}", response_model=StudentDetail)
async def get_student(student_id: uuid.UUID, ctx: CtxDep) -> StudentDetail:
    ctx.require(Cap.VIEW_ROSTER)
    s = await _get_student(ctx, student_id)
    # Contact details are decrypted only for roles that send messages.
    show_contacts = ctx.principal.can(Cap.RELEASE_MESSAGES)
    guardians = []
    for link in s.guardian_links:
        g: Guardian = await ctx.session.get(Guardian, link.guardian_id)  # type: ignore[assignment]
        guardians.append(
            GuardianSummary(
                id=g.id,
                full_name=g.full_name,
                relationship=link.relationship_label,
                preferred_channel=g.preferred_channel.value,
                language=g.language.value,
                email=decrypt_field(g.email_enc) if show_contacts else None,
                phone=decrypt_field(g.phone_enc) if show_contacts else None,
                whatsapp=decrypt_field(g.whatsapp_enc) if show_contacts else None,
            )
        )
    squads = [
        {
            "squad_id": m.squad_id,
            "squad_name": sq.name,
            "role": m.role,
            "is_reserve": m.is_reserve,
            "status": m.status.value,
            "joined_on": m.joined_on,
        }
        for m, sq in (
            await ctx.session.execute(
                select(SquadMembership, Squad)
                .join(Squad, Squad.id == SquadMembership.squad_id)
                .where(SquadMembership.student_id == s.id)
            )
        ).all()
    ]
    flags = [
        {
            "id": f.id,
            "kind": f.kind.value,
            "rule": f.rule,
            "explanation": f.explanation,
            "raised_at": f.raised_at,
            "claim_type": "inferred",
        }
        for f in (
            await ctx.session.scalars(
                select(StudentFlag).where(StudentFlag.student_id == s.id, StudentFlag.resolved_at.is_(None))
            )
        ).all()
    ]
    ctx.audit("student.read", "student", s.id, context={"contacts_decrypted": show_contacts})
    base = StudentOut.model_validate(s).model_dump()
    return StudentDetail(**base, guardians=guardians, squads=squads, open_flags=flags)


async def build_profile(ctx, s: Student) -> dict:  # noqa: ANN001
    """Skills earned with evidence, participation history, progression, attendance, goals."""
    awards = (
        await ctx.session.scalars(
            select(SkillAward).where(SkillAward.student_id == s.id).order_by(SkillAward.awarded_on)
        )
    ).all()
    best: dict[uuid.UUID, SkillAward] = {}
    for a in awards:
        if a.status == AwardStatus.VERIFIED and (a.skill_id not in best or a.level >= best[a.skill_id].level):
            best[a.skill_id] = a
    skills: list[dict[str, Any]] = [
        {
            "skill_id": a.skill_id,
            "code": a.skill.code,
            "name": a.skill.name,
            "domain": a.skill.domain,
            "strand": a.skill.strand,
            "parent_label_en": a.skill.parent_label_en,
            "parent_label_ar": a.skill.parent_label_ar,
            "level": a.level,
            "level_name": LEVELS[a.level],
            "latest_award": award_out(a).model_dump(),
            "claim_type": "measured",
        }
        for a in sorted(best.values(), key=lambda a: (a.skill.domain, a.skill.code))
    ]
    timeline = [award_out(a).model_dump() for a in awards]
    results = (
        await ctx.session.execute(
            select(Result, CompetitionEdition)
            .join(ResultParticipant, ResultParticipant.result_id == Result.id)
            .join(CompetitionEdition, CompetitionEdition.id == Result.edition_id)
            .where(ResultParticipant.student_id == s.id)
            .order_by(CompetitionEdition.event_starts.desc())
        )
    ).all()
    history = [
        {
            "result_id": r.id,
            "edition_id": ed.id,
            "edition_name": ed.name,
            "competition_name": ed.competition.name,
            "tier": ed.tier.value,
            "event_starts": ed.event_starts,
            "placement": r.placement,
            "field_size": r.field_size,
            "performance_index": r.performance_index,
            "award_title": r.award_title,
            "data_quality_flags": r.data_quality_flags,
            "claim_type": "measured",
        }
        for r, ed in results
    ]
    att = (
        await ctx.session.execute(
            select(Attendance.status, func.count())
            .where(Attendance.student_id == s.id)
            .group_by(Attendance.status)
        )
    ).all()
    counts = {st.value: n for st, n in att}
    counted = sum(n for st, n in att if st != AttendanceStatus.EXCUSED)
    attended = counts.get("present", 0) + counts.get("late", 0)
    goals = [
        {**GoalOut.model_validate(g).model_dump(), "skill_code": g.skill.code, "skill_name": g.skill.name}
        for g in (await ctx.session.scalars(select(SkillGoal).where(SkillGoal.student_id == s.id))).all()
    ]
    by_domain: dict[str, int] = {}
    for sk in skills:
        by_domain[sk["domain"]] = by_domain.get(sk["domain"], 0) + 1
    return {
        "student": StudentOut.model_validate(s).model_dump(),
        "skills": skills,
        "skills_by_domain": by_domain,
        "award_timeline": timeline,
        "results": history,
        "attendance": {
            "counts": counts,
            "attended": attended,
            "denominator": counted,
            "rate_percent": round(100 * attended / counted) if counted else None,
        },
        "goals": goals,
    }


@router.get("/{student_id}/profile")
async def student_profile(student_id: uuid.UUID, ctx: CtxDep) -> dict:
    ctx.require(Cap.VIEW_ROSTER)
    s = await _get_student(ctx, student_id)
    profile = await build_profile(ctx, s)
    ctx.audit("student.profile_read", "student", s.id)
    return profile


@router.get("/{student_id}/readiness")
async def student_readiness(student_id: uuid.UUID, edition_id: uuid.UUID, ctx: CtxDep) -> dict:
    ctx.require(Cap.RUN_READINESS)
    s = await _get_student(ctx, student_id)
    edition = await ctx.session.get(CompetitionEdition, edition_id)
    if edition is None:
        raise not_found("Edition not found")
    res = (await readiness_for(ctx.session, [s.id], edition))[s.id]
    ctx.audit("student.readiness_read", "student", s.id, context={"edition_id": edition_id})
    out = res.as_dict()
    out["threshold_percent"] = int(ctx.settings.readiness_threshold * 100)
    out["edition_name"] = edition.name
    return out


@router.get("/{student_id}/recommendations")
async def student_recommendations(student_id: uuid.UUID, ctx: CtxDep) -> dict:
    ctx.require(Cap.RUN_READINESS)
    s = await _get_student(ctx, student_id)
    out = await competition_recommendations(ctx.session, ctx.settings, s, datetime.now(UTC).date())
    ctx.audit("student.recommendations_read", "student", s.id)
    return out


@router.get("/{student_id}/export")
async def student_export(student_id: uuid.UUID, ctx: CtxDep, reason: str = Query(min_length=3)) -> Response:
    """Subject-access bundle: everything held about one student, as a readable ZIP."""
    ctx.require(Cap.SUBJECT_ACCESS_EXPORT)
    s = await _get_student(ctx, student_id)
    from app.services.sar import build_sar_bundle

    data, filename = await build_sar_bundle(ctx, s)
    ctx.audit("student.subject_access_export", "student", s.id, reason=reason, context={"bytes": len(data)})
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{student_id}/goals", response_model=list[GoalOut])
async def list_goals(student_id: uuid.UUID, ctx: CtxDep) -> list[GoalOut]:
    ctx.require(Cap.VIEW_ROSTER)
    s = await _get_student(ctx, student_id)
    rows = (await ctx.session.scalars(select(SkillGoal).where(SkillGoal.student_id == s.id))).all()
    return [GoalOut.model_validate(g) for g in rows]


@router.post("/{student_id}/goals", response_model=GoalOut, status_code=201)
async def create_goal(student_id: uuid.UUID, body: GoalIn, ctx: CtxDep) -> GoalOut:
    ctx.require(Cap.VERIFY_SKILLS)
    s = await _get_student(ctx, student_id)
    goal = SkillGoal(student_id=s.id, created_by_id=ctx.user_id, **body.model_dump())
    ctx.session.add(goal)
    await ctx.session.flush()
    ctx.audit("goal.create", "student", s.id, context={"goal_id": goal.id, "skill_id": body.skill_id})
    return GoalOut.model_validate(goal)


class GoalPatch(BaseModel):
    status: str


@router.patch("/{student_id}/goals/{goal_id}", response_model=GoalOut)
async def update_goal(student_id: uuid.UUID, goal_id: uuid.UUID, body: GoalPatch, ctx: CtxDep) -> GoalOut:
    ctx.require(Cap.VERIFY_SKILLS)
    s = await _get_student(ctx, student_id)
    goal = await ctx.session.get(SkillGoal, goal_id)
    if goal is None or goal.student_id != s.id:
        raise not_found()
    if body.status not in ("open", "achieved", "dropped"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid status")
    goal.status = body.status  # type: ignore[assignment]
    ctx.audit("goal.update", "student", s.id, context={"goal_id": goal.id, "status": body.status})
    return GoalOut.model_validate(goal)
