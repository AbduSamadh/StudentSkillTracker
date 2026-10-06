"""Offline-capable capture: training sessions, attendance, results (spec §5.3, §5.4, §11).

Sync contract
-------------
* Every offline-capable POST accepts a client-generated ``idempotency_key``. A replay
  returns the canonical record (HTTP 200 + ``Idempotent-Replay: true``) and never
  creates a duplicate.
* Concurrent edits resolve server-side, last-write-wins on scalar fields, ordered by the
  client's ``client_modified_at``. A stale write is ignored and the canonical record is
  returned with ``X-Sync-Conflict: stale``.
* A result already released to parents cannot be changed except by an admin override,
  which requires a reason and is audit-logged.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import or_, select

from app.deps import CtxDep
from app.models import (
    Attendance,
    CompetitionEdition,
    Result,
    ResultParticipant,
    SkillAward,
    SquadMembership,
    TrainingSession,
)
from app.models.enums import AwardStatus, MembershipStatus, Role, WebhookEvent
from app.permissions import (
    Cap,
    ensure_can_write_squad,
    ensure_squad_in_scope,
    ensure_student_in_scope,
    not_found,
    staff_student_scope,
)
from app.schemas.domain import (
    AttendanceIn,
    AttendanceOut,
    ResultIn,
    ResultOut,
    ResultPatch,
    SessionIn,
    SessionOut,
)
from app.services.awards import propose_from_rubric
from app.services.performance import performance_index
from app.services.webhooks import emit

router = APIRouter(tags=["capture"])


def _is_newer(incoming: datetime | None, current: datetime | None) -> bool:
    """Last-write-wins: apply unless the server already holds a newer client edit."""
    if incoming is None or current is None:
        return True
    return incoming >= current


# ---------------- Training sessions ----------------
@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    ctx: CtxDep,
    squad_id: uuid.UUID | None = None,
    start: datetime | None = Query(None, alias="from"),
    end: datetime | None = Query(None, alias="to"),
) -> list[SessionOut]:
    ctx.require(Cap.VIEW_ROSTER)
    stmt = select(TrainingSession).order_by(TrainingSession.starts_at.desc())
    if squad_id:
        ensure_squad_in_scope(ctx.principal, squad_id)
        stmt = stmt.where(TrainingSession.squad_id == squad_id)
    elif not ctx.principal.whole_school:
        stmt = stmt.where(TrainingSession.squad_id.in_(ctx.principal.coached_squad_ids or {uuid.UUID(int=0)}))
    if start:
        stmt = stmt.where(TrainingSession.starts_at >= start)
    if end:
        stmt = stmt.where(TrainingSession.starts_at <= end)
    return [SessionOut.model_validate(s) for s in (await ctx.session.scalars(stmt.limit(200))).all()]


@router.post("/sessions", response_model=SessionOut, status_code=201)
async def create_session(body: SessionIn, ctx: CtxDep, response: Response) -> SessionOut:
    ensure_can_write_squad(ctx.principal, body.squad_id, Cap.RECORD_ATTENDANCE)
    if body.idempotency_key:
        existing = await ctx.session.scalar(
            select(TrainingSession).where(TrainingSession.idempotency_key == body.idempotency_key)
        )
        if existing is not None:
            response.status_code = 200
            response.headers["Idempotent-Replay"] = "true"
            return SessionOut.model_validate(existing)
    if body.id is not None:
        existing = await ctx.session.get(TrainingSession, body.id)
        if existing is not None:
            if existing.squad_id != body.squad_id:
                raise HTTPException(status.HTTP_409_CONFLICT, "Session belongs to another squad")
            response.status_code = 200
            if _is_newer(body.client_modified_at, existing.client_modified_at):
                for k in ("starts_at", "ends_at", "location", "notes", "skill_ids", "client_modified_at"):
                    setattr(existing, k, getattr(body, k))
            else:
                response.headers["X-Sync-Conflict"] = "stale"
            return SessionOut.model_validate(existing)
    data = body.model_dump(exclude={"id"})
    s = TrainingSession(id=body.id or uuid.uuid4(), created_by_id=ctx.user_id, **data)
    ctx.session.add(s)
    await ctx.session.flush()
    ctx.audit("session.create", "squad", body.squad_id, context={"session_id": s.id})
    return SessionOut.model_validate(s)


async def _session(ctx, session_id: uuid.UUID) -> TrainingSession:  # noqa: ANN001
    s = await ctx.session.get(TrainingSession, session_id)
    if s is None:
        raise not_found("Session not found")
    ensure_squad_in_scope(ctx.principal, s.squad_id)
    return s


@router.get("/sessions/{session_id}")
async def get_session(session_id: uuid.UUID, ctx: CtxDep) -> dict:
    ctx.require(Cap.VIEW_ROSTER)
    s = await _session(ctx, session_id)
    rows = (await ctx.session.scalars(select(Attendance).where(Attendance.session_id == s.id))).all()
    return {
        **SessionOut.model_validate(s).model_dump(),
        "attendance": [AttendanceOut.model_validate(a) for a in rows],
    }


@router.post("/sessions/{session_id}/attendance", response_model=list[AttendanceOut])
async def record_attendance(
    session_id: uuid.UUID, body: AttendanceIn, ctx: CtxDep, response: Response
) -> list[AttendanceOut]:
    """Idempotent upsert per (session, student); safe to replay from the offline queue."""
    s = await _session(ctx, session_id)
    ensure_can_write_squad(ctx.principal, s.squad_id, Cap.RECORD_ATTENDANCE)
    members = set(
        await ctx.session.scalars(
            select(SquadMembership.student_id).where(
                SquadMembership.squad_id == s.squad_id, SquadMembership.status == MembershipStatus.ACTIVE
            )
        )
    )
    unknown = [r.student_id for r in body.records if r.student_id not in members]
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Not in this squad: {[str(u) for u in unknown]}"
        )
    existing = {
        a.student_id: a
        for a in (await ctx.session.scalars(select(Attendance).where(Attendance.session_id == s.id))).all()
    }
    stale = 0
    for rec in body.records:
        a = existing.get(rec.student_id)
        if a is None:
            a = Attendance(
                session_id=s.id,
                student_id=rec.student_id,
                status=rec.status,
                note=rec.note,
                recorded_by_id=ctx.user_id,
                client_modified_at=rec.client_modified_at,
            )
            ctx.session.add(a)
            existing[rec.student_id] = a
        elif _is_newer(rec.client_modified_at, a.client_modified_at):
            a.status, a.note, a.client_modified_at, a.recorded_by_id = (
                rec.status,
                rec.note,
                rec.client_modified_at,
                ctx.user_id,
            )
        else:
            stale += 1
    if stale:
        response.headers["X-Sync-Conflict"] = f"stale:{stale}"
    await ctx.session.flush()
    ctx.audit(
        "attendance.record", "squad", s.squad_id, context={"session_id": s.id, "count": len(body.records)}
    )
    return [AttendanceOut.model_validate(a) for a in existing.values()]


# ---------------- Results ----------------
async def _result_out(ctx, r: Result) -> ResultOut:  # noqa: ANN001
    out = ResultOut.model_validate(r)
    out.participant_ids = [p.student_id for p in r.participants]
    out.proposed_award_count = len(
        (
            await ctx.session.scalars(
                select(SkillAward.id).where(
                    SkillAward.result_id == r.id, SkillAward.status == AwardStatus.PROPOSED
                )
            )
        ).all()
    )
    return out


async def _apply_index(ctx, r: Result, edition: CompetitionEdition) -> None:  # noqa: ANN001
    idx = performance_index(r.placement, r.field_size, ctx.settings.tier_weight(edition.tier))
    r.performance_index = idx.value
    r.data_quality_flags = idx.flags


async def _check_result_scope(ctx, r: Result) -> None:  # noqa: ANN001
    if ctx.principal.whole_school:
        return
    if r.squad_id and r.squad_id in ctx.principal.coached_squad_ids:
        return
    scope = await staff_student_scope(ctx.session, ctx.principal)
    if not any(p.student_id in (scope or set()) for p in r.participants):
        raise not_found("Result not found")


@router.get("/results", response_model=list[ResultOut])
async def list_results(
    ctx: CtxDep, edition_id: uuid.UUID | None = None, squad_id: uuid.UUID | None = None
) -> list[ResultOut]:
    ctx.require(Cap.VIEW_ROSTER)
    stmt = select(Result).order_by(Result.created_at.desc())
    if edition_id:
        stmt = stmt.where(Result.edition_id == edition_id)
    if squad_id:
        ensure_squad_in_scope(ctx.principal, squad_id)
        stmt = stmt.where(Result.squad_id == squad_id)
    if not ctx.principal.whole_school:
        scope = await staff_student_scope(ctx.session, ctx.principal) or frozenset()
        stmt = stmt.where(
            or_(
                Result.squad_id.in_(ctx.principal.coached_squad_ids or {uuid.UUID(int=0)}),
                Result.id.in_(
                    select(ResultParticipant.result_id).where(
                        ResultParticipant.student_id.in_(scope or {uuid.UUID(int=0)})
                    )
                ),
            )
        )
    return [await _result_out(ctx, r) for r in (await ctx.session.scalars(stmt.limit(300))).all()]


@router.post("/results", response_model=ResultOut, status_code=201)
async def record_result(body: ResultIn, ctx: CtxDep, response: Response) -> ResultOut:
    ctx.require(Cap.RECORD_RESULTS)
    if body.idempotency_key:
        existing = await ctx.session.scalar(
            select(Result).where(Result.idempotency_key == body.idempotency_key)
        )
        if existing is not None:
            await _check_result_scope(ctx, existing)
            response.status_code = 200
            response.headers["Idempotent-Replay"] = "true"
            return await _result_out(ctx, existing)
    if body.id is not None and await ctx.session.get(Result, body.id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Result exists; use PATCH /results/{id}")
    if Role.PROGRAMME_ADMIN not in ctx.principal.roles:
        if body.squad_id is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "squad_id is required")
        ensure_can_write_squad(ctx.principal, body.squad_id, Cap.RECORD_RESULTS)
    for sid in body.participant_ids:
        await ensure_student_in_scope(ctx.session, ctx.principal, sid)
    edition = await ctx.session.get(CompetitionEdition, body.edition_id)
    if edition is None:
        raise not_found("Edition not found")
    memberships = {}
    if body.squad_id:
        memberships = {
            m.student_id: m.id
            for m in (
                await ctx.session.scalars(
                    select(SquadMembership).where(SquadMembership.squad_id == body.squad_id)
                )
            ).all()
        }
    r = Result(
        id=body.id or uuid.uuid4(),
        recorded_by_id=ctx.user_id,
        **body.model_dump(exclude={"id", "participant_ids", "rubric_scores"}),
        rubric_scores={k: float(v) for k, v in body.rubric_scores.items()},
    )
    r.participants = [
        ResultParticipant(student_id=sid, membership_id=memberships.get(sid)) for sid in body.participant_ids
    ]
    await _apply_index(ctx, r, edition)
    ctx.session.add(r)
    await ctx.session.flush()
    proposed = await propose_from_rubric(ctx.session, r, edition)
    await emit(
        ctx.session,
        WebhookEvent.RESULT_RECORDED,
        {
            "result_id": r.id,
            "edition_id": edition.id,
            "participant_ids": body.participant_ids,
            "placement": r.placement,
            "field_size": r.field_size,
            "performance_index": float(r.performance_index) if r.performance_index is not None else None,
        },
    )
    ctx.defer("deliver_webhooks")
    ctx.audit(
        "result.record",
        "edition",
        edition.id,
        context={
            "result_id": r.id,
            "proposed_awards": len(proposed),
            "data_quality_flags": r.data_quality_flags,
        },
    )
    return await _result_out(ctx, r)


async def _result(ctx, result_id: uuid.UUID) -> Result:  # noqa: ANN001
    r = await ctx.session.get(Result, result_id)
    if r is None:
        raise not_found("Result not found")
    await _check_result_scope(ctx, r)
    return r


@router.get("/results/{result_id}", response_model=ResultOut)
async def get_result(result_id: uuid.UUID, ctx: CtxDep) -> ResultOut:
    ctx.require(Cap.VIEW_ROSTER)
    return await _result_out(ctx, await _result(ctx, result_id))


@router.patch("/results/{result_id}", response_model=ResultOut)
async def update_result(
    result_id: uuid.UUID, body: ResultPatch, ctx: CtxDep, response: Response
) -> ResultOut:
    ctx.require(Cap.RECORD_RESULTS)
    r = await _result(ctx, result_id)
    if r.squad_id and Role.PROGRAMME_ADMIN not in ctx.principal.roles:
        ensure_can_write_squad(ctx.principal, r.squad_id, Cap.RECORD_RESULTS)
    if r.released_at is not None:
        if not body.admin_override or Role.PROGRAMME_ADMIN not in ctx.principal.roles:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This result has been released to parents. A programme admin must override, with a reason.",
            )
        if not body.override_reason:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "override_reason is required")
    if not _is_newer(body.client_modified_at, r.client_modified_at):
        response.headers["X-Sync-Conflict"] = "stale"
        return await _result_out(ctx, r)
    before = {k: getattr(r, k) for k in ("placement", "field_size", "score", "max_score", "award_title")}
    changes = body.model_dump(exclude_unset=True, exclude={"admin_override", "override_reason"})
    if "rubric_scores" in changes and changes["rubric_scores"] is not None:
        changes["rubric_scores"] = {k: float(v) for k, v in changes["rubric_scores"].items()}
    for k, v in changes.items():
        setattr(r, k, v)
    edition = await ctx.session.get(CompetitionEdition, r.edition_id)
    assert edition is not None
    await _apply_index(ctx, r, edition)
    await ctx.session.flush()
    await propose_from_rubric(ctx.session, r, edition)
    if r.released_at is not None:
        ctx.audit(
            "result.admin_override",
            "edition",
            r.edition_id,
            reason=body.override_reason,
            context={"result_id": r.id, "before": before, "after": changes},
        )
    else:
        ctx.audit(
            "result.update", "edition", r.edition_id, context={"result_id": r.id, "changes": list(changes)}
        )
    return await _result_out(ctx, r)


@router.get("/results/{result_id}/index")
async def result_index(result_id: uuid.UUID, ctx: CtxDep) -> dict:
    ctx.require(Cap.VIEW_ROSTER)
    r = await _result(ctx, result_id)
    edition = await ctx.session.get(CompetitionEdition, r.edition_id)
    assert edition is not None
    idx = performance_index(r.placement, r.field_size, ctx.settings.tier_weight(edition.tier))
    return {
        "result_id": r.id,
        "performance_index": float(idx.value) if idx.value is not None else None,
        "data_quality_flags": idx.flags,
        "components": idx.components,
        "tier": edition.tier.value,
        "claim_type": "measured",
    }


@router.post("/results/{result_id}/release", response_model=ResultOut)
async def release_result(result_id: uuid.UUID, ctx: CtxDep) -> ResultOut:
    """Mark a result as published to parents; later edits then need an admin override."""
    ctx.require(Cap.RELEASE_MESSAGES)
    r = await _result(ctx, result_id)
    if r.released_at is None:
        r.released_at, r.released_by_id = datetime.now(UTC), ctx.user_id
    ctx.audit("result.release", "edition", r.edition_id, context={"result_id": r.id})
    return await _result_out(ctx, r)
