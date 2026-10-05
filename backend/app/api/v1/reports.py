import uuid
from datetime import UTC, datetime

import jwt
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select

from app.db import tenant_session
from app.deps import CtxDep
from app.models import Insight, ReportJob, Student, StudentFlag
from app.models.enums import ExportFormat, FlagKind, InsightStatus, JobStatus, ReportType, WebhookEvent
from app.permissions import Cap, apply_student_scope, ensure_student_in_scope, not_found, staff_student_scope
from app.security import decode_scoped_token
from app.services import storage
from app.services.flags import sync_flags
from app.services.insights import generate_for_student, half_term
from app.services.reporting.builders import ReportCtx, build, leader_dashboard
from app.services.reporting.jobs import CONTENT_TYPES
from app.services.reporting.render import to_html
from app.services.webhooks import emit

router = APIRouter(tags=["reports"])

# Which capability each report needs (spec §6.2 audiences + §2 permissions).
REPORT_CAPS = {
    ReportType.STUDENT_PROFILE: Cap.GENERATE_REPORTS,
    ReportType.SQUAD_READINESS: Cap.RUN_READINESS,
    ReportType.SEASON_REVIEW: Cap.VIEW_SCHOOL_ANALYTICS,
    ReportType.COHORT_COVERAGE: Cap.VIEW_SCHOOL_ANALYTICS,
    ReportType.INSPECTION_EVIDENCE: Cap.EXPORT_INSPECTION,
    ReportType.PLATEAU_STRETCH: Cap.RUN_READINESS,
    ReportType.KIT_UTILISATION: Cap.MANAGE_INVENTORY,
}


def _rc(ctx) -> ReportCtx:  # noqa: ANN001
    return ReportCtx(session=ctx.session, settings=ctx.settings, principal=ctx.principal,
                     tenant_name=ctx.tenant.name, today=datetime.now(UTC).date())


class GenerateIn(BaseModel):
    format: ExportFormat = ExportFormat.PDF
    params: dict[str, str] = {}


class JobOut(BaseModel):
    id: uuid.UUID
    report_type: ReportType
    format: ExportFormat
    status: JobStatus
    filename: str | None
    error: str | None
    created_at: datetime
    completed_at: datetime | None
    download_url: str | None = None


def _job_out(ctx, job: ReportJob) -> JobOut:  # noqa: ANN001
    url = None
    if job.status == JobStatus.SUCCEEDED and job.storage_key:
        url = storage.signed_url(ctx.tenant.id, job.storage_key, job.filename or "report", CONTENT_TYPES[job.format])
    return JobOut(id=job.id, report_type=job.report_type, format=job.format, status=job.status, filename=job.filename,
                  error=job.error, created_at=job.created_at, completed_at=job.completed_at, download_url=url)


@router.post("/reports/{report_type}/generate", response_model=JobOut, status_code=202)
async def generate_report(report_type: ReportType, body: GenerateIn, ctx: CtxDep) -> JobOut:
    ctx.require(REPORT_CAPS[report_type])
    if report_type == ReportType.STUDENT_PROFILE and body.params.get("student_id"):
        await ensure_student_in_scope(ctx.session, ctx.principal, uuid.UUID(body.params["student_id"]))
    job = ReportJob(report_type=report_type, format=body.format, params=body.params, requested_by_id=ctx.user_id)
    ctx.session.add(job)
    await ctx.session.flush()
    ctx.audit("report.generate", "report_job", job.id, context={"type": report_type.value, "params": body.params,
                                                                "format": body.format.value})
    ctx.defer("generate_report", job_id=job.id)
    return _job_out(ctx, job)


@router.get("/reports/jobs", response_model=list[JobOut])
async def list_jobs(ctx: CtxDep) -> list[JobOut]:
    ctx.require(Cap.GENERATE_REPORTS)
    rows = (await ctx.session.scalars(select(ReportJob).where(ReportJob.requested_by_id == ctx.user_id)
                                      .order_by(ReportJob.created_at.desc()).limit(50))).all()
    return [_job_out(ctx, j) for j in rows]


@router.get("/reports/jobs/{job_id}", response_model=JobOut)
async def get_job(job_id: uuid.UUID, ctx: CtxDep) -> JobOut:
    ctx.require(Cap.GENERATE_REPORTS)
    job = await ctx.session.get(ReportJob, job_id)
    if job is None or job.requested_by_id != ctx.user_id:
        raise not_found("Job not found")
    return _job_out(ctx, job)


@router.get("/reports/{report_type}/preview", response_model=None)
async def preview_report(report_type: ReportType, ctx: CtxDep, student_id: str | None = None,
                         squad_id: str | None = None, edition_id: str | None = None, season_id: str | None = None,
                         as_html: bool = False) -> Response | dict:
    """Synchronous JSON (or print-optimised HTML) rendering of the same report body."""
    ctx.require(REPORT_CAPS[report_type])
    params = {k: v for k, v in {"student_id": student_id, "squad_id": squad_id, "edition_id": edition_id,
                                "season_id": season_id}.items() if v}
    body = await build(_rc(ctx), report_type, params)
    ctx.audit("report.preview", "report", None, context={"type": report_type.value, "params": params})
    if as_html:
        return Response(to_html(body, school=ctx.tenant.name, brand=ctx.settings.branding.primary,
                                user_name=ctx.principal.display_name), media_type="text/html")
    return body.as_dict()


@router.get("/files/{token}")
async def download_file(token: str) -> Response:
    """Local-storage signed URL (S3 deployments use presigned URLs directly)."""
    try:
        data = decode_scoped_token(token, "file")
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Link expired") from exc
    tid = uuid.UUID(data["tid"])
    if not data["key"].startswith(f"{tid}/"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid link")
    async with tenant_session(tid):
        content = await storage.get(data["key"])
    return Response(content, media_type=data["ct"], headers={"Content-Disposition": f'attachment; filename="{data["fn"]}"'})


@router.get("/dashboard/leader")
async def dashboard_leader(ctx: CtxDep) -> dict:
    """Six numbers, not sixty."""
    ctx.require(Cap.VIEW_SCHOOL_ANALYTICS)
    return await leader_dashboard(_rc(ctx))


# ---------------- Flags ----------------
@router.get("/flags")
async def list_flags(ctx: CtxDep, kind: FlagKind | None = None) -> list[dict]:
    ctx.require(Cap.RUN_READINESS)
    scope = await staff_student_scope(ctx.session, ctx.principal)
    stmt = apply_student_scope(select(StudentFlag, Student).join(Student, Student.id == StudentFlag.student_id),
                               Student.id, scope).where(StudentFlag.resolved_at.is_(None))
    if kind:
        stmt = stmt.where(StudentFlag.kind == kind)
    rows = (await ctx.session.execute(stmt.order_by(StudentFlag.raised_at.desc()))).all()
    return [{"id": f.id, "student_id": s.id, "student_name": s.display_name, "year_group": s.year_group,
             "kind": f.kind.value, "rule": f.rule, "explanation": f.explanation, "facts": f.facts,
             "raised_at": f.raised_at, "claim_type": "inferred"} for f, s in rows]


@router.post("/flags/refresh")
async def refresh_flags(ctx: CtxDep) -> dict:
    """Re-evaluate flags for every student in the caller's scope (also runs nightly)."""
    ctx.require(Cap.RUN_READINESS)
    scope = await staff_student_scope(ctx.session, ctx.principal)
    students = list((await ctx.session.scalars(apply_student_scope(select(Student), Student.id, scope))).all())
    raised = await sync_flags(ctx.session, ctx.settings, students, datetime.now(UTC).date())
    for f in raised:
        await emit(ctx.session, WebhookEvent.STUDENT_FLAGGED, {"student_id": f.student_id, "kind": f.kind.value,
                                                                "rule": f.rule})
    ctx.defer("deliver_webhooks")
    ctx.audit("flags.refresh", None, None, context={"students": len(students), "raised": len(raised)})
    return {"evaluated": len(students), "raised": len(raised)}


class ResolveIn(BaseModel):
    note: str | None = None


@router.post("/flags/{flag_id}/resolve")
async def resolve_flag(flag_id: uuid.UUID, body: ResolveIn, ctx: CtxDep) -> dict:
    ctx.require(Cap.RUN_READINESS)
    f = await ctx.session.get(StudentFlag, flag_id)
    if f is None:
        raise not_found()
    await ensure_student_in_scope(ctx.session, ctx.principal, f.student_id)
    f.resolved_at, f.resolved_by_id = datetime.now(UTC), ctx.user_id
    ctx.audit("flag.resolve", "student", f.student_id, reason=body.note, context={"flag_id": f.id})
    return {"ok": True}


# ---------------- Insights ----------------
class InsightsGenerateIn(BaseModel):
    student_ids: list[uuid.UUID] = []
    squad_id: uuid.UUID | None = None


@router.post("/insights/generate")
async def generate_insights(body: InsightsGenerateIn, ctx: CtxDep) -> dict:
    ctx.require(Cap.REVIEW_INSIGHTS)
    scope = await staff_student_scope(ctx.session, ctx.principal)
    stmt = apply_student_scope(select(Student), Student.id, scope)
    if body.student_ids:
        stmt = stmt.where(Student.id.in_(body.student_ids))
    if body.squad_id:
        from app.models import SquadMembership

        stmt = stmt.where(Student.id.in_(select(SquadMembership.student_id).where(
            SquadMembership.squad_id == body.squad_id)))
    today = datetime.now(UTC).date()
    n = 0
    for s in (await ctx.session.scalars(stmt)).all():
        n += len(await generate_for_student(ctx.session, ctx.settings, s, today))
    ctx.audit("insights.generate", None, None, context={"insights": n, "period": half_term(today)[0]})
    return {"period": half_term(today)[0], "insights": n}


@router.get("/insights")
async def list_insights(ctx: CtxDep, student_id: uuid.UUID | None = None, status_: InsightStatus | None = Query(None, alias="status"),
                        period: str | None = None) -> list[dict]:
    ctx.require(Cap.REVIEW_INSIGHTS)
    scope = await staff_student_scope(ctx.session, ctx.principal)
    stmt = apply_student_scope(select(Insight, Student).join(Student, Student.id == Insight.student_id), Student.id, scope)
    if student_id:
        stmt = stmt.where(Insight.student_id == student_id)
    if status_:
        stmt = stmt.where(Insight.status == status_)
    if period:
        stmt = stmt.where(Insight.period == period)
    rows = (await ctx.session.execute(stmt.order_by(Insight.created_at.desc()).limit(300))).all()
    return [{"id": i.id, "student_id": s.id, "student_name": s.display_name, "period": i.period, "rule": i.rule,
             "claim_type": i.claim_type.value, "text_en": i.edited_text_en or i.text_en,
             "text_ar": i.edited_text_ar or i.text_ar, "generated_en": i.text_en, "facts": i.facts,
             "status": i.status.value, "reviewed_at": i.reviewed_at} for i, s in rows]


class ReviewIn(BaseModel):
    decision: InsightStatus
    edited_text_en: str | None = None
    edited_text_ar: str | None = None


@router.post("/insights/{insight_id}/review")
async def review_insight(insight_id: uuid.UUID, body: ReviewIn, ctx: CtxDep) -> dict:
    """Every generated sentence is reviewable by a teacher before it reaches a parent."""
    ctx.require(Cap.REVIEW_INSIGHTS)
    i = await ctx.session.get(Insight, insight_id)
    if i is None:
        raise not_found()
    await ensure_student_in_scope(ctx.session, ctx.principal, i.student_id)
    if body.decision == InsightStatus.DRAFT:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose approved or rejected")
    i.status, i.reviewed_by_id, i.reviewed_at = body.decision, ctx.user_id, datetime.now(UTC)
    if body.edited_text_en:
        i.edited_text_en = body.edited_text_en
    if body.edited_text_ar:
        i.edited_text_ar = body.edited_text_ar
    ctx.audit("insight.review", "student", i.student_id, context={"insight_id": i.id, "decision": body.decision.value,
                                                                 "edited": bool(body.edited_text_en or body.edited_text_ar)})
    return {"ok": True}


@router.post("/messages/progress-reports", status_code=201)
async def draft_progress_reports(ctx: CtxDep, squad_id: uuid.UUID | None = None) -> dict:
    """Half-term progress report: only students with teacher-approved insights are included;
    an admin then previews and releases."""
    ctx.require(Cap.RELEASE_MESSAGES)
    from app.services.messaging.auto import draft_progress_reports as draft

    period = half_term(datetime.now(UTC).date())[0]
    stmt = select(Insight.student_id).where(Insight.status == InsightStatus.APPROVED, Insight.period == period)
    if squad_id:
        from app.models import SquadMembership

        stmt = stmt.where(Insight.student_id.in_(select(SquadMembership.student_id).where(
            SquadMembership.squad_id == squad_id)))
    ids = list(dict.fromkeys(await ctx.session.scalars(stmt)))
    m = await draft(ctx.session, ids, period, ctx.user_id)
    if m is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No approved insights or no approved template")
    ctx.audit("message.progress_reports_drafted", "message", m.id, context={"students": len(ids)})
    return {"message_id": m.id, "students": len(ids), "period": period}
