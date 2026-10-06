"""Configurable retention per record type, purged by a scheduled job (spec §8.2).

Leavers: once a student has been gone longer than ``student_after_leaving_months`` they are
anonymised — identifiers, names and dates of birth are removed, guardian links cut, and
their media deleted — while aggregate facts (results, award counts) remain, so historic
season figures do not change. Their media is deleted earlier, after
``media_after_leaving_months``.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import CursorResult, Delete, delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ImportBatch,
    MediaAsset,
    MediaSubject,
    MessageDelivery,
    OutboxEntry,
    ReportJob,
    Student,
    StudentGuardian,
)
from app.models.enums import EnrolmentStatus
from app.services import storage
from app.tenancy import TenantSettings


def _months(n: int) -> timedelta:
    return timedelta(days=round(n * 30.44))


async def _delete(session: AsyncSession, stmt: Delete) -> int:
    res = cast(CursorResult, await session.execute(stmt))
    return res.rowcount or 0


async def purge(session: AsyncSession, settings: TenantSettings, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    r = settings.retention
    out: dict[str, int] = {}

    out["outbox"] = await _delete(
        session, delete(OutboxEntry).where(OutboxEntry.created_at < now - _months(r.outbox_months))
    )
    out["message_deliveries"] = await _delete(
        session,
        delete(MessageDelivery).where(
            MessageDelivery.created_at < now - _months(r.message_deliveries_months)
        ),
    )
    out["import_batches"] = await _delete(
        session, delete(ImportBatch).where(ImportBatch.created_at < now - _months(r.import_batches_months))
    )

    old_jobs = (
        await session.scalars(
            select(ReportJob).where(ReportJob.created_at < now - _months(r.report_exports_months))
        )
    ).all()
    for j in old_jobs:
        if j.storage_key:
            await storage.delete(j.storage_key)
        await session.delete(j)
    out["report_exports"] = len(old_jobs)

    # Media of leavers.
    media_cutoff = (now - _months(r.media_after_leaving_months)).date()
    leaver_ids = list(
        await session.scalars(
            select(Student.id).where(
                Student.enrolment_status == EnrolmentStatus.LEFT, Student.left_on < media_cutoff
            )
        )
    )
    media = (
        (
            await session.scalars(
                select(MediaAsset)
                .join(MediaSubject, MediaSubject.media_id == MediaAsset.id)
                .where(MediaSubject.student_id.in_(leaver_ids or [uuid.UUID(int=0)]))
            )
        )
        .unique()
        .all()
    )
    for m in media:
        await storage.delete(m.storage_key)
        await session.delete(m)
    out["leaver_media"] = len(media)

    # Anonymise long-gone leavers.
    anon_cutoff = (now - _months(r.student_after_leaving_months)).date()
    to_anon = (
        await session.scalars(
            select(Student).where(
                Student.enrolment_status == EnrolmentStatus.LEFT,
                Student.left_on < anon_cutoff,
                Student.anonymised_at.is_(None),
            )
        )
    ).all()
    for s in to_anon:
        s.given_name, s.family_name, s.preferred_name, s.full_name_ar = "Former", "student", None, None
        s.external_mis_id = f"anon-{s.id.hex[:12]}"
        s.date_of_birth, s.house, s.user_id, s.anonymised_at = None, None, None, now
        await session.execute(delete(StudentGuardian).where(StudentGuardian.student_id == s.id))
    out["students_anonymised"] = len(to_anon)

    # Audit events via the definer function (refuses anything younger than one year).
    audit_cutoff = now - _months(max(r.audit_events_months, 12))
    out["audit_events"] = (
        await session.execute(text("SELECT purge_audit_events(:t)"), {"t": audit_cutoff})
    ).scalar_one()
    await session.flush()
    return out
