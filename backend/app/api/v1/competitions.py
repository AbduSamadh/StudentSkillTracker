import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.deps import CtxDep
from app.models import (
    Competition,
    CompetitionEdition,
    ExamWindow,
    Season,
    Skill,
    SkillRequirement,
    Student,
)
from app.models.enums import CompetitionStatus, EnrolmentStatus
from app.permissions import Cap, apply_student_scope, not_found, staff_student_scope
from app.schemas.domain import (
    CompetitionIn,
    CompetitionOut,
    EditionIn,
    EditionOut,
    ExamWindowIn,
    ExamWindowOut,
    RequirementIn,
    SeasonIn,
    SeasonOut,
)
from app.services.clashes import clashes_for_edition
from app.services.readiness import effective_requirements, readiness_for

router = APIRouter(tags=["competitions"])


def edition_out(e: CompetitionEdition) -> EditionOut:
    out = EditionOut.model_validate(e)
    out.competition_name = e.competition.name if e.competition else None
    return out


# ---------------- Seasons and exam windows ----------------
@router.get("/seasons", response_model=list[SeasonOut])
async def list_seasons(ctx: CtxDep) -> list[SeasonOut]:
    ctx.require(Cap.VIEW_ROSTER)
    return [SeasonOut.model_validate(s) for s in (await ctx.session.scalars(select(Season).order_by(Season.starts_on.desc()))).all()]


@router.post("/seasons", response_model=SeasonOut, status_code=201)
async def create_season(body: SeasonIn, ctx: CtxDep) -> SeasonOut:
    ctx.require(Cap.MANAGE_COMPETITIONS)
    s = Season(**body.model_dump())
    ctx.session.add(s)
    await ctx.session.flush()
    ctx.audit("season.create", "season", s.id)
    return SeasonOut.model_validate(s)


@router.patch("/seasons/{season_id}", response_model=SeasonOut)
async def update_season(season_id: uuid.UUID, body: SeasonIn, ctx: CtxDep) -> SeasonOut:
    """Admins edit seasons; changing the budget envelope is a leader decision."""
    s = await ctx.session.get(Season, season_id)
    if s is None:
        raise not_found()
    if body.budget_envelope != s.budget_envelope:
        ctx.require(Cap.APPROVE_SPEND)
    else:
        ctx.require(Cap.MANAGE_COMPETITIONS)
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    ctx.audit("season.update", "season", s.id, context=body.model_dump())
    return SeasonOut.model_validate(s)


@router.get("/exam-windows", response_model=list[ExamWindowOut])
async def list_exam_windows(ctx: CtxDep) -> list[ExamWindowOut]:
    ctx.require(Cap.VIEW_ROSTER)
    rows = (await ctx.session.scalars(select(ExamWindow).order_by(ExamWindow.starts_on))).all()
    return [ExamWindowOut.model_validate(w) for w in rows]


@router.post("/exam-windows", response_model=ExamWindowOut, status_code=201)
async def create_exam_window(body: ExamWindowIn, ctx: CtxDep) -> ExamWindowOut:
    ctx.require(Cap.MANAGE_COMPETITIONS)
    w = ExamWindow(**body.model_dump())
    ctx.session.add(w)
    await ctx.session.flush()
    ctx.audit("exam_window.create", "exam_window", w.id)
    return ExamWindowOut.model_validate(w)


@router.delete("/exam-windows/{window_id}", status_code=204)
async def delete_exam_window(window_id: uuid.UUID, ctx: CtxDep) -> None:
    ctx.require(Cap.MANAGE_COMPETITIONS)
    w = await ctx.session.get(ExamWindow, window_id)
    if w is None:
        raise not_found()
    await ctx.session.delete(w)
    ctx.audit("exam_window.delete", "exam_window", window_id)


# ---------------- Competitions ----------------
@router.get("/competitions", response_model=list[CompetitionOut])
async def list_competitions(
    ctx: CtxDep, discipline: str | None = None, status_: CompetitionStatus | None = Query(None, alias="status")
) -> list[CompetitionOut]:
    ctx.require(Cap.VIEW_ROSTER)
    stmt = select(Competition).order_by(Competition.name)
    if discipline:
        stmt = stmt.where(Competition.discipline == discipline)
    if status_:
        stmt = stmt.where(Competition.status == status_)
    return [CompetitionOut.model_validate(c) for c in (await ctx.session.scalars(stmt)).all()]


@router.post("/competitions", response_model=CompetitionOut, status_code=201)
async def create_competition(body: CompetitionIn, ctx: CtxDep) -> CompetitionOut:
    """Admins create competitions; teachers can only propose them."""
    ctx.require(Cap.PROPOSE_COMPETITION)
    is_admin = ctx.principal.can(Cap.MANAGE_COMPETITIONS)
    c = Competition(
        **body.model_dump(),
        status=CompetitionStatus.ACTIVE if is_admin else CompetitionStatus.PROPOSED,
        proposed_by_id=ctx.user_id,
        approved_by_id=ctx.user_id if is_admin else None,
    )
    ctx.session.add(c)
    await ctx.session.flush()
    ctx.audit("competition.create" if is_admin else "competition.propose", "competition", c.id)
    return CompetitionOut.model_validate(c)


async def _competition(ctx, cid: uuid.UUID) -> Competition:  # noqa: ANN001
    c = await ctx.session.get(Competition, cid)
    if c is None:
        raise not_found("Competition not found")
    return c


@router.get("/competitions/{competition_id}", response_model=CompetitionOut)
async def get_competition(competition_id: uuid.UUID, ctx: CtxDep) -> CompetitionOut:
    ctx.require(Cap.VIEW_ROSTER)
    return CompetitionOut.model_validate(await _competition(ctx, competition_id))


@router.patch("/competitions/{competition_id}", response_model=CompetitionOut)
async def update_competition(competition_id: uuid.UUID, body: CompetitionIn, ctx: CtxDep) -> CompetitionOut:
    ctx.require(Cap.MANAGE_COMPETITIONS)
    c = await _competition(ctx, competition_id)
    for k, v in body.model_dump().items():
        setattr(c, k, v)
    ctx.audit("competition.update", "competition", c.id)
    return CompetitionOut.model_validate(c)


@router.post("/competitions/{competition_id}/approve", response_model=CompetitionOut)
async def approve_competition(competition_id: uuid.UUID, ctx: CtxDep) -> CompetitionOut:
    ctx.require(Cap.MANAGE_COMPETITIONS)
    c = await _competition(ctx, competition_id)
    c.status, c.approved_by_id = CompetitionStatus.ACTIVE, ctx.user_id
    ctx.audit("competition.approve", "competition", c.id)
    return CompetitionOut.model_validate(c)


async def _resolve_skill(ctx, body: RequirementIn) -> Skill:  # noqa: ANN001
    if body.skill_id:
        skill = await ctx.session.get(Skill, body.skill_id)
    elif body.skill_code:
        skill = await ctx.session.scalar(select(Skill).where(Skill.code == body.skill_code))
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "skill_id or skill_code is required")
    if skill is None:
        raise not_found("Skill not found")
    return skill


async def _upsert_requirement(ctx, competition_id: uuid.UUID, edition_id: uuid.UUID | None, body: RequirementIn) -> None:  # noqa: ANN001
    skill = await _resolve_skill(ctx, body)
    stmt = select(SkillRequirement).where(
        SkillRequirement.competition_id == competition_id, SkillRequirement.skill_id == skill.id
    )
    stmt = stmt.where(SkillRequirement.edition_id.is_(None)) if edition_id is None else stmt.where(
        SkillRequirement.edition_id == edition_id
    )
    req = await ctx.session.scalar(stmt)
    if req is None:
        req = SkillRequirement(competition_id=competition_id, edition_id=edition_id, skill_id=skill.id,
                               required_level=body.required_level)
        ctx.session.add(req)
    req.required_level, req.weight, req.is_core, req.removed = body.required_level, body.weight, body.is_core, body.removed


def _req_dict(r) -> dict:  # noqa: ANN001
    return {
        "skill_id": r.skill_id, "code": r.code, "name": r.name, "domain": r.domain,
        "required_level": r.required_level, "weight": float(r.weight), "is_core": r.is_core,
        "inherited": r.inherited, "requirement_id": r.requirement_id,
    }


@router.get("/competitions/{competition_id}/requirements")
async def competition_requirements(competition_id: uuid.UUID, ctx: CtxDep) -> list[dict]:
    ctx.require(Cap.VIEW_ROSTER)
    rows = (
        await ctx.session.execute(
            select(SkillRequirement, Skill)
            .join(Skill, Skill.id == SkillRequirement.skill_id)
            .where(SkillRequirement.competition_id == competition_id, SkillRequirement.edition_id.is_(None))
            .order_by(Skill.code)
        )
    ).all()
    return [
        {"skill_id": s.id, "code": s.code, "name": s.name, "domain": s.domain, "required_level": r.required_level,
         "weight": float(r.weight), "is_core": r.is_core, "requirement_id": r.id}
        for r, s in rows
    ]


@router.post("/competitions/{competition_id}/requirements", status_code=201)
async def set_competition_requirements(competition_id: uuid.UUID, body: list[RequirementIn], ctx: CtxDep) -> list[dict]:
    """Requirements set once on the competition are inherited by every edition."""
    ctx.require(Cap.MANAGE_COMPETITIONS)
    await _competition(ctx, competition_id)
    for item in body:
        await _upsert_requirement(ctx, competition_id, None, item)
    await ctx.session.flush()
    ctx.audit("competition.requirements_set", "competition", competition_id, context={"count": len(body)})
    return await competition_requirements(competition_id, ctx)


# ---------------- Editions ----------------
@router.get("/competitions/{competition_id}/editions", response_model=list[EditionOut])
async def list_competition_editions(competition_id: uuid.UUID, ctx: CtxDep) -> list[EditionOut]:
    ctx.require(Cap.VIEW_ROSTER)
    rows = (
        await ctx.session.scalars(
            select(CompetitionEdition)
            .where(CompetitionEdition.competition_id == competition_id)
            .order_by(CompetitionEdition.event_starts.desc())
        )
    ).all()
    return [edition_out(e) for e in rows]


@router.post("/competitions/{competition_id}/editions", response_model=EditionOut, status_code=201)
async def create_edition(competition_id: uuid.UUID, body: EditionIn, ctx: CtxDep) -> EditionOut:
    ctx.require(Cap.MANAGE_COMPETITIONS)
    c = await _competition(ctx, competition_id)
    data = body.model_dump()
    data["tier"] = data["tier"] or c.tier
    data["rubric"] = [r.model_dump(mode="json") for r in body.rubric]
    e = CompetitionEdition(competition_id=c.id, **data)
    ctx.session.add(e)
    await ctx.session.flush()
    await ctx.session.refresh(e, ["competition"])
    ctx.audit("edition.create", "edition", e.id)
    return edition_out(e)


@router.get("/editions", response_model=list[EditionOut])
async def list_editions(
    ctx: CtxDep, upcoming: bool = False, season_id: uuid.UUID | None = None, limit: int = Query(100, le=500)
) -> list[EditionOut]:
    ctx.require(Cap.VIEW_ROSTER)
    stmt = select(CompetitionEdition).order_by(CompetitionEdition.event_starts)
    if upcoming:
        stmt = stmt.where(CompetitionEdition.event_ends >= datetime.now(UTC).date())
    if season_id:
        stmt = stmt.where(CompetitionEdition.season_id == season_id)
    return [edition_out(e) for e in (await ctx.session.scalars(stmt.limit(limit))).all()]


async def _edition(ctx, eid: uuid.UUID) -> CompetitionEdition:  # noqa: ANN001
    e = await ctx.session.get(CompetitionEdition, eid)
    if e is None:
        raise not_found("Edition not found")
    return e


@router.get("/editions/{edition_id}", response_model=EditionOut)
async def get_edition(edition_id: uuid.UUID, ctx: CtxDep) -> EditionOut:
    ctx.require(Cap.VIEW_ROSTER)
    return edition_out(await _edition(ctx, edition_id))


@router.patch("/editions/{edition_id}", response_model=EditionOut)
async def update_edition(edition_id: uuid.UUID, body: EditionIn, ctx: CtxDep) -> EditionOut:
    ctx.require(Cap.MANAGE_COMPETITIONS)
    e = await _edition(ctx, edition_id)
    data = body.model_dump()
    data["tier"] = data["tier"] or e.tier
    data["rubric"] = [r.model_dump(mode="json") for r in body.rubric]
    for k, v in data.items():
        setattr(e, k, v)
    ctx.audit("edition.update", "edition", e.id)
    return edition_out(e)


@router.get("/editions/{edition_id}/requirements")
async def edition_requirements(edition_id: uuid.UUID, ctx: CtxDep) -> list[dict]:
    """Effective requirements: inherited from the competition, overlaid by edition rows."""
    ctx.require(Cap.VIEW_ROSTER)
    e = await _edition(ctx, edition_id)
    return [_req_dict(r) for r in await effective_requirements(ctx.session, e)]


@router.post("/editions/{edition_id}/requirements", status_code=201)
async def set_edition_requirements(edition_id: uuid.UUID, body: list[RequirementIn], ctx: CtxDep) -> list[dict]:
    """Override or remove inherited requirements for one edition (e.g. a final needs Secure)."""
    ctx.require(Cap.MANAGE_COMPETITIONS)
    e = await _edition(ctx, edition_id)
    for item in body:
        await _upsert_requirement(ctx, e.competition_id, e.id, item)
    await ctx.session.flush()
    ctx.audit("edition.requirements_set", "edition", e.id, context={"count": len(body)})
    return [_req_dict(r) for r in await effective_requirements(ctx.session, e)]


@router.get("/editions/{edition_id}/clashes")
async def edition_clashes(edition_id: uuid.UUID, ctx: CtxDep) -> list[dict]:
    ctx.require(Cap.VIEW_ROSTER)
    return await clashes_for_edition(ctx.session, await _edition(ctx, edition_id))


@router.get("/editions/{edition_id}/eligible-students")
async def eligible_students(edition_id: uuid.UUID, ctx: CtxDep, limit: int = Query(100, le=500)) -> dict:
    """Students in the caller's scope who are eligible by year group, ranked by readiness."""
    ctx.require(Cap.RUN_READINESS)
    e = await _edition(ctx, edition_id)
    scope = await staff_student_scope(ctx.session, ctx.principal)
    stmt = apply_student_scope(select(Student), Student.id, scope).where(
        Student.enrolment_status == EnrolmentStatus.ACTIVE
    )
    if e.eligible_year_min:
        stmt = stmt.where(Student.year_group >= e.eligible_year_min)
    if e.eligible_year_max:
        stmt = stmt.where(Student.year_group <= e.eligible_year_max)
    students = (await ctx.session.scalars(stmt)).all()
    results = await readiness_for(ctx.session, [s.id for s in students], e)
    threshold = ctx.settings.readiness_threshold
    ranked = sorted(
        (
            {
                "student_id": s.id,
                "name": s.display_name,
                "year_group": s.year_group,
                "percent": results[s.id].percent,
                "ready": results[s.id].score is not None and results[s.id].score >= threshold,
                "gap_count": len(results[s.id].gaps),
                "gap_codes": [g.requirement.code for g in results[s.id].gaps],
            }
            for s in students
        ),
        key=lambda r: (-(r["percent"] or 0), r["gap_count"], r["name"]),
    )[:limit]
    ctx.audit("edition.eligible_students_read", "edition", e.id, context={"student_ids": [r["student_id"] for r in ranked]})
    return {
        "edition_id": e.id,
        "edition_name": e.name,
        "threshold_percent": int(threshold * 100),
        "requirements_catalogued": bool(ranked and ranked[0]["percent"] is not None),
        "students": ranked,
        "claim_type": "inferred",
    }

