"""Scheduled jobs run by the worker for every tenant."""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app import audit
from app.db import anonymous_session, tenant_session
from app.models import Student, Tenant
from app.models.enums import EnrolmentStatus, FlagKind, WebhookEvent
from app.services.flags import sync_flags
from app.services.insights import generate_for_student, half_term
from app.services.messaging.auto import draft_attendance_concern
from app.services.retention import purge
from app.services.webhooks import deliver_pending, emit
from app.tenancy import load_settings

log = logging.getLogger(__name__)


async def tenant_ids() -> list[uuid.UUID]:
    async with anonymous_session() as s:
        return list(await s.scalars(select(Tenant.id)))


async def refresh_flags_job(tenant_id: str, **_: object) -> int:
    tid = uuid.UUID(tenant_id)
    async with tenant_session(tid) as session:
        tenant = await session.get(Tenant, tid)
        assert tenant is not None
        settings = load_settings(tenant.settings)
        students = list(
            (
                await session.scalars(
                    select(Student).where(Student.enrolment_status == EnrolmentStatus.ACTIVE)
                )
            ).all()
        )
        raised = await sync_flags(session, settings, students, datetime.now(UTC).date())
        for f in raised:
            await emit(
                session,
                WebhookEvent.STUDENT_FLAGGED,
                {"student_id": f.student_id, "kind": f.kind.value, "rule": f.rule},
            )
            if f.kind == FlagKind.ATTENDANCE:
                # Rule-triggered, but drafted only: a person sends attendance concerns.
                await draft_attendance_concern(session, f.student_id, None)
        audit.record(
            session,
            actor_user_id=None,
            actor_roles=["system"],
            action="flags.nightly",
            context={"students": len(students), "raised": len(raised)},
        )
        await session.commit()
    await deliver_pending(tenant_id)
    return len(raised)


async def insights_job(tenant_id: str, **_: object) -> int:
    tid = uuid.UUID(tenant_id)
    async with tenant_session(tid) as session:
        tenant = await session.get(Tenant, tid)
        assert tenant is not None
        settings = load_settings(tenant.settings)
        today = datetime.now(UTC).date()
        n = 0
        for s in (
            await session.scalars(select(Student).where(Student.enrolment_status == EnrolmentStatus.ACTIVE))
        ).all():
            n += len(await generate_for_student(session, settings, s, today))
        audit.record(
            session,
            actor_user_id=None,
            actor_roles=["system"],
            action="insights.generate",
            context={"period": half_term(today)[0], "insights": n},
        )
        await session.commit()
        return n


async def retention_job(tenant_id: str, **_: object) -> dict:
    tid = uuid.UUID(tenant_id)
    async with tenant_session(tid) as session:
        tenant = await session.get(Tenant, tid)
        assert tenant is not None
        out = await purge(session, load_settings(tenant.settings))
        audit.record(
            session, actor_user_id=None, actor_roles=["system"], action="retention.purge", context=out
        )
        await session.commit()
        return out
