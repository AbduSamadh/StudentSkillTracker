"""Async report generation: POST /reports/{type}/generate -> job id -> GET /reports/jobs/{id}."""

import logging
import re
import uuid
from datetime import UTC, datetime

from app.db import tenant_session
from app.deps import load_principal
from app.models import ReportJob, Tenant, User
from app.models.enums import ExportFormat, JobStatus
from app.services import storage
from app.services.reporting.builders import ReportCtx, build
from app.services.reporting.render import to_csv, to_html, to_pdf, to_xlsx
from app.tenancy import load_settings

log = logging.getLogger(__name__)

CONTENT_TYPES = {
    ExportFormat.PDF: "application/pdf",
    ExportFormat.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ExportFormat.CSV: "text/csv; charset=utf-8",
    ExportFormat.HTML: "text/html; charset=utf-8",
}


async def generate_report_job(tenant_id: str, job_id: str, **_: object) -> None:
    tid = uuid.UUID(tenant_id)
    async with tenant_session(tid) as session:
        job = await session.get(ReportJob, uuid.UUID(job_id))
        tenant = await session.get(Tenant, tid)
        if job is None or tenant is None:
            return
        job.status, job.started_at = JobStatus.RUNNING, datetime.now(UTC)
        await session.commit()
        try:
            user = await session.get(User, job.requested_by_id)
            assert user is not None
            # Jobs run with the requesting user's scope — a report cannot see more than its author.
            principal = await load_principal(session, user.id, tid, mfa=True)
            settings = load_settings(tenant.settings)
            rc = ReportCtx(session=session, settings=settings, principal=principal, tenant_name=tenant.name,
                           today=datetime.now(UTC).date())
            body = await build(rc, job.report_type, job.params)
            kw = {"school": tenant.name, "user_name": user.display_name}
            if job.format == ExportFormat.PDF:
                data = to_pdf(body, brand=settings.branding.primary, **kw)
            elif job.format == ExportFormat.HTML:
                data = to_html(body, brand=settings.branding.primary, **kw).encode()
            elif job.format == ExportFormat.XLSX:
                data = to_xlsx(body, **kw)
            else:
                data = to_csv(body, **kw)
            slug = re.sub(r"[^a-z0-9]+", "-", body.title.lower()).strip("-")[:60]
            job.filename = f"{slug}-{datetime.now(UTC):%Y%m%d}.{job.format.value}"
            job.storage_key = f"{tid}/reports/{job.id}/{job.filename}"
            await storage.put(job.storage_key, data, CONTENT_TYPES[job.format])
            job.status, job.completed_at = JobStatus.SUCCEEDED, datetime.now(UTC)
        except Exception as exc:  # noqa: BLE001
            log.exception("report job %s failed", job_id)
            job.status, job.error = JobStatus.FAILED, str(getattr(exc, "detail", exc))[:500]
            job.completed_at = datetime.now(UTC)
        await session.commit()
