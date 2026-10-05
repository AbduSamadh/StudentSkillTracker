import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func, select

from app.deps import CtxDep
from app.models import Skill, SkillAward, SquadMembership, Student, TrainingSession
from app.models.enums import AwardSource, AwardStatus, EnrolmentStatus, MembershipStatus, Role
from app.permissions import (
    Cap,
    apply_student_scope,
    ensure_can_write_squad,
    ensure_squad_in_scope,
    ensure_student_in_scope,
    not_found,
    staff_student_scope,
)
from app.schemas.domain import AwardIn, AwardOut, BulkConfirmIn, QuickTagIn, RevokeIn, SkillIn, SkillOut
from app.services.awards import award_out, confirm, create_verified
from app.services.reporting.suppression import Figure

router = APIRouter(prefix="/skills", tags=["skills"])


@router.get("", response_model=list[SkillOut])
async def list_skills(
    ctx: CtxDep,
    domain: str | None = None,
    q: str | None = Query(None, max_length=80),
    include_inactive: bool = False,
) -> list[SkillOut]:
    """The taxonomy."""
    stmt = select(Skill).order_by(Skill.code)
    if domain:
        stmt = stmt.where(Skill.domain == domain)
    if not include_inactive:
        stmt = stmt.where(Skill.is_active.is_(True))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(func.lower(Skill.name).like(like) | func.lower(Skill.code).like(like))
    return [SkillOut.model_validate(s) for s in (await ctx.session.scalars(stmt)).all()]


@router.post("", response_model=SkillOut, status_code=201)
async def create_skill(body: SkillIn, ctx: CtxDep) -> SkillOut:
    ctx.require(Cap.MANAGE_COMPETITIONS)
    if await ctx.session.scalar(select(Skill).where(Skill.code == body.code)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A skill with this code exists")
    s = Skill(**body.model_dump())
    ctx.session.add(s)
    await ctx.session.flush()
    ctx.audit("skill.create", "skill", s.id)
    return SkillOut.model_validate(s)


@router.patch("/{skill_id}", response_model=SkillOut)
async def update_skill(skill_id: uuid.UUID, body: SkillIn, ctx: CtxDep) -> SkillOut:
    """Codes are stable identifiers and cannot be changed; deactivate instead of deleting."""
    ctx.require(Cap.MANAGE_COMPETITIONS)
    s = await ctx.session.get(Skill, skill_id)
    if s is None:
        raise not_found()
    if body.code != s.code:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Skill codes are stable and cannot be changed"
        )
    for k, v in body.model_dump(exclude={"code"}).items():
        setattr(s, k, v)
    ctx.audit("skill.update", "skill", s.id)
    return SkillOut.model_validate(s)


async def _teacher_can_write_student(ctx, student_id: uuid.UUID) -> None:  # noqa: ANN001
    """Verify skills: teachers for students in their own squads; admins for anyone."""
    ctx.require(Cap.VERIFY_SKILLS)
    await ensure_student_in_scope(ctx.session, ctx.principal, student_id)


@router.post("/awards", response_model=AwardOut, status_code=201)
async def grant_award(body: AwardIn, ctx: CtxDep, response: Response) -> AwardOut:
    """Grant a verified award (teacher verification or reviewed artefact)."""
    await _teacher_can_write_student(ctx, body.student_id)
    if body.session_id:
        ts = await ctx.session.get(TrainingSession, body.session_id)
        if ts is None:
            raise not_found("Session not found")
        ensure_squad_in_scope(ctx.principal, ts.squad_id)
    award, created = await create_verified(
        ctx.session, verified_by=ctx.user_id, **body.model_dump(exclude={"source"}), source=body.source
    )
    if not created:
        response.status_code = 200
        response.headers["Idempotent-Replay"] = "true"
    else:
        ctx.audit(
            "award.grant",
            "student",
            body.student_id,
            context={
                "award_id": award.id,
                "skill_id": body.skill_id,
                "level": body.level,
                "source": body.source,
            },
        )
        ctx.defer("deliver_webhooks")
    return award_out(award)


@router.post("/awards/quick-tag", response_model=list[AwardOut], status_code=201)
async def quick_tag(body: QuickTagIn, ctx: CtxDep) -> list[AwardOut]:
    """One skill, one level, several students, one tap — keeps tagging under 30s per student."""
    ctx.require(Cap.VERIFY_SKILLS)
    if body.session_id:
        ts = await ctx.session.get(TrainingSession, body.session_id)
        if ts is None:
            raise not_found("Session not found")
        ensure_can_write_squad(ctx.principal, ts.squad_id, Cap.VERIFY_SKILLS)
    out = []
    for sid in body.student_ids:
        await ensure_student_in_scope(ctx.session, ctx.principal, sid)
        key = f"{body.idempotency_key}:{sid}" if body.idempotency_key else None
        award, created = await create_verified(
            ctx.session,
            student_id=sid,
            skill_id=body.skill_id,
            level=body.level,
            source=AwardSource.TEACHER,
            verified_by=ctx.user_id,
            evidence_note=body.evidence_note,
            session_id=body.session_id,
            idempotency_key=key,
        )
        if created:
            ctx.audit(
                "award.grant",
                "student",
                sid,
                context={
                    "award_id": award.id,
                    "skill_id": body.skill_id,
                    "level": body.level,
                    "via": "quick_tag",
                },
            )
        out.append(award_out(award))
    ctx.defer("deliver_webhooks")
    return out


@router.get("/awards/proposed", response_model=list[AwardOut])
async def proposed_awards(
    ctx: CtxDep,
    squad_id: uuid.UUID | None = None,
    result_id: uuid.UUID | None = None,
    student_id: uuid.UUID | None = None,
) -> list[AwardOut]:
    """Batch-confirm screen: proposed awards from rubric imports and self-assessments."""
    ctx.require(Cap.VERIFY_SKILLS)
    scope = await staff_student_scope(ctx.session, ctx.principal)
    stmt = apply_student_scope(select(SkillAward), SkillAward.student_id, scope).where(
        SkillAward.status == AwardStatus.PROPOSED
    )
    if squad_id:
        ensure_squad_in_scope(ctx.principal, squad_id)
        stmt = stmt.where(
            SkillAward.student_id.in_(
                select(SquadMembership.student_id).where(
                    SquadMembership.squad_id == squad_id, SquadMembership.status == MembershipStatus.ACTIVE
                )
            )
        )
    if result_id:
        stmt = stmt.where(SkillAward.result_id == result_id)
    if student_id:
        stmt = stmt.where(SkillAward.student_id == student_id)
    rows = (await ctx.session.scalars(stmt.order_by(SkillAward.created_at))).all()
    names = {
        st.id: st.display_name
        for st in (
            await ctx.session.scalars(
                select(Student).where(Student.id.in_({a.student_id for a in rows} or {uuid.UUID(int=0)}))
            )
        ).all()
    }
    out = []
    for a in rows:
        o = award_out(a)
        o.student_name = names.get(a.student_id)
        out.append(o)
    return out


@router.post("/awards/bulk-confirm")
async def bulk_confirm(body: BulkConfirmIn, ctx: CtxDep) -> dict:
    """A named teacher confirms (or rejects) proposed awards in one batch."""
    ctx.require(Cap.VERIFY_SKILLS)
    ids = list(dict.fromkeys(body.confirm + body.reject))
    awards = {
        a.id: a for a in (await ctx.session.scalars(select(SkillAward).where(SkillAward.id.in_(ids)))).all()
    }
    for aid in ids:
        a = awards.get(aid)
        if a is None:
            raise not_found(f"Award {aid} not found")
        await ensure_student_in_scope(ctx.session, ctx.principal, a.student_id)
    confirmed, rejected = [], []
    for aid in body.confirm:
        lvl = body.level_overrides.get(aid)
        if lvl is not None and not 1 <= lvl <= 4:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Levels are 1–4")
        await confirm(ctx.session, awards[aid], ctx.user_id, lvl)
        confirmed.append(aid)
        ctx.audit(
            "award.confirm",
            "student",
            awards[aid].student_id,
            context={"award_id": aid, "level": awards[aid].level, "source": awards[aid].source.value},
        )
    for aid in body.reject:
        a = awards[aid]
        if a.status != AwardStatus.PROPOSED:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Award {aid} is not proposed")
        a.status = AwardStatus.REJECTED
        a.verified_by_id, a.verified_at = ctx.user_id, datetime.now(UTC)
        rejected.append(aid)
        ctx.audit("award.reject", "student", a.student_id, context={"award_id": aid})
    ctx.defer("deliver_webhooks")
    return {"confirmed": confirmed, "rejected": rejected}


@router.delete("/awards/{award_id}", response_model=AwardOut)
async def revoke_award(award_id: uuid.UUID, body: RevokeIn, ctx: CtxDep) -> AwardOut:
    """Revoke, with a reason. The award is kept (status=revoked) for the audit trail."""
    ctx.require(Cap.VERIFY_SKILLS)
    a = await ctx.session.get(SkillAward, award_id)
    if a is None:
        raise not_found("Award not found")
    await ensure_student_in_scope(ctx.session, ctx.principal, a.student_id)
    if a.status == AwardStatus.REVOKED:
        return award_out(a)
    a.status, a.revoked_by_id, a.revoked_at, a.revoke_reason = (
        AwardStatus.REVOKED,
        ctx.user_id,
        datetime.now(UTC),
        body.reason,
    )
    ctx.audit("award.revoke", "student", a.student_id, reason=body.reason, context={"award_id": a.id})
    return award_out(a)


@router.get("/coverage")
async def coverage(ctx: CtxDep) -> dict:
    """Taxonomy coverage across the cohort: which skills have any verified evidence, and how
    many students hold them. Small cells are suppressed (spec §6.1)."""
    ctx.require(Cap.VIEW_SCHOOL_ANALYTICS)
    settings = ctx.settings
    skills = (await ctx.session.scalars(select(Skill).where(Skill.is_active.is_(True)))).all()
    active_students = (
        await ctx.session.scalar(
            select(func.count())
            .select_from(Student)
            .where(Student.enrolment_status == EnrolmentStatus.ACTIVE)
        )
        or 0
    )
    holders = dict(
        (
            await ctx.session.execute(
                select(SkillAward.skill_id, func.count(func.distinct(SkillAward.student_id)))
                .join(Student, Student.id == SkillAward.student_id)
                .where(
                    SkillAward.status == AwardStatus.VERIFIED,
                    Student.enrolment_status == EnrolmentStatus.ACTIVE,
                )
                .group_by(SkillAward.skill_id)
            )
        ).all()
    )
    by_domain: dict[str, dict] = {}
    for s in skills:
        d = by_domain.setdefault(
            s.domain, {"domain": s.domain, "skills_total": 0, "skills_evidenced": 0, "skills": []}
        )
        d["skills_total"] += 1
        n = holders.get(s.id, 0)
        if n:
            d["skills_evidenced"] += 1
        d["skills"].append(
            {"code": s.code, "name": s.name, "holders": Figure.count(n, settings, label=s.code).as_dict()}
        )
    total = len(skills)
    evidenced = sum(1 for s in skills if holders.get(s.id))
    return {
        "taxonomy_coverage": Figure.percent(
            evidenced,
            total,
            settings,
            label="skills with any verified evidence",
            denominator_label="active skills in the taxonomy",
            apply_base_rule=False,
        ).as_dict(),
        "active_students": active_students,
        "domains": [
            {
                **d,
                "coverage": Figure.percent(
                    d["skills_evidenced"],
                    d["skills_total"],
                    settings,
                    label=f"{d['domain']} skills evidenced",
                    denominator_label=f"{d['domain']} skills",
                    apply_base_rule=False,
                ).as_dict(),
            }
            for d in sorted(by_domain.values(), key=lambda d: d["domain"])
        ],
    }


@router.post("/awards/self-assess", response_model=AwardOut, status_code=201)
async def self_assess(body: AwardIn, ctx: CtxDep) -> AwardOut:
    """Students record a self-assessment; it is LOW confidence and never counts until a
    teacher countersigns it via bulk-confirm."""
    ctx.require(Cap.PORTAL_STUDENT)
    if body.student_id not in ctx.principal.own_student_ids:
        raise not_found("Student not found")
    if Role.STUDENT not in ctx.principal.roles:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Students only")
    a = SkillAward(
        student_id=body.student_id,
        skill_id=body.skill_id,
        level=body.level,
        awarded_on=body.awarded_on or datetime.now(UTC).date(),
        source=AwardSource.SELF,
        status=AwardStatus.PROPOSED,
        evidence_note=body.evidence_note,
        proposed_by_id=ctx.user_id,
        idempotency_key=body.idempotency_key,
    )
    ctx.session.add(a)
    await ctx.session.flush()
    await ctx.session.refresh(a, ["skill"])
    ctx.audit("award.self_assess", "student", body.student_id, context={"award_id": a.id})
    return award_out(a)
