"""Consent is a record, not a boolean (spec §8.2). The current state for a (student,
purpose[, edition]) is the most recent decision; a withdrawal is a timestamp on it.

Defaults when no record exists:
* media            -> NOT consented (opt-in; enforced in the data layer views)
* communications   -> consented (programme messaging is part of enrolment), until a
                      guardian declines or withdraws — which blocks the next send.
* travel / fee     -> NOT consented (must be requested per edition)
* data_processing  -> NOT consented for under-13s (Child Digital Safety law requires
                      verifiable parental consent); assumed under the school's
                      enrolment contract for 13+.
"""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Consent, Student
from app.models.enums import ConsentDecision, ConsentPurpose

DEFAULT_WITHOUT_RECORD = {
    ConsentPurpose.MEDIA: False,
    ConsentPurpose.COMMUNICATIONS: True,
    ConsentPurpose.TRAVEL: False,
    ConsentPurpose.FEE_AUTHORISATION: False,
}


def _age(dob: date | None, today: date) -> int | None:
    if dob is None:
        return None
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


async def latest(
    session: AsyncSession, student_id: uuid.UUID, purpose: ConsentPurpose, edition_id: uuid.UUID | None = None,
    guardian_id: uuid.UUID | None = None,
) -> Consent | None:
    stmt = select(Consent).where(Consent.student_id == student_id, Consent.purpose == purpose)
    stmt = stmt.where(Consent.edition_id == edition_id) if edition_id else stmt.where(Consent.edition_id.is_(None))
    if guardian_id:
        stmt = stmt.where(Consent.guardian_id == guardian_id)
    return await session.scalar(stmt.order_by(Consent.decided_at.desc()).limit(1))


def is_active(c: Consent | None) -> bool:
    return c is not None and c.decision == ConsentDecision.GRANTED and c.withdrawn_at is None


async def has_consent(
    session: AsyncSession, student: Student, purpose: ConsentPurpose, *, edition_id: uuid.UUID | None = None,
    guardian_id: uuid.UUID | None = None,
) -> bool:
    c = await latest(session, student.id, purpose, edition_id, guardian_id)
    if c is not None:
        return is_active(c)
    if purpose == ConsentPurpose.DATA_PROCESSING:
        age = _age(student.date_of_birth, datetime.now(UTC).date())
        return age is not None and age >= 13
    return DEFAULT_WITHOUT_RECORD.get(purpose, False)


async def communications_blocked(session: AsyncSession, student_id: uuid.UUID, guardian_id: uuid.UUID) -> bool:
    """True if this guardian has declined or withdrawn communications consent for the child."""
    c = await latest(session, student_id, ConsentPurpose.COMMUNICATIONS, guardian_id=guardian_id)
    return c is not None and not is_active(c)


async def media_consented_ids(session: AsyncSession, student_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Reads the data-layer view so the rule is defined once, in the database."""
    if not student_ids:
        return set()
    rows = await session.execute(
        text("SELECT student_id FROM student_media_consent WHERE has_media_consent AND student_id = ANY(:ids)"),
        {"ids": list(student_ids)},
    )
    return {r[0] for r in rows}
