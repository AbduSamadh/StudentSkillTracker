"""Skill award lifecycle (spec §4.3).

* Teacher verification / artefact review -> VERIFIED immediately, verified_by = that teacher.
* Rubric criterion at a competition -> PROPOSED; a teacher batch-confirms. A result never
  grants every mapped skill automatically: entering is not demonstrating.
* Self-assessment -> PROPOSED; counts only once a teacher countersigns.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CompetitionEdition, Result, Skill, SkillAward
from app.models.enums import AWARD_SOURCE_CONFIDENCE, AwardSource, AwardStatus, WebhookEvent
from app.schemas.domain import AwardOut
from app.services.webhooks import emit


def award_out(a: SkillAward) -> AwardOut:
    out = AwardOut.model_validate(a)
    out.skill_code = a.skill.code if a.skill else None
    out.skill_name = a.skill.name if a.skill else None
    out.confidence = AWARD_SOURCE_CONFIDENCE[a.source]
    out.claim_type = "measured" if a.status == AwardStatus.VERIFIED else "inferred"
    return out


async def create_verified(
    session: AsyncSession,
    *,
    student_id: uuid.UUID,
    skill_id: uuid.UUID,
    level: int,
    source: AwardSource,
    verified_by: uuid.UUID,
    awarded_on: date | None = None,
    evidence_note: str | None = None,
    artefact_media_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
) -> tuple[SkillAward, bool]:
    if source == AwardSource.RUBRIC:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Rubric awards are proposed from results, then confirmed"
        )
    if source == AwardSource.SELF:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Self-assessments must be countersigned, not granted"
        )
    if source == AwardSource.ARTEFACT and artefact_media_id is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "An artefact award needs the artefact attached"
        )
    if idempotency_key:
        existing = await session.scalar(
            select(SkillAward).where(SkillAward.idempotency_key == idempotency_key)
        )
        if existing is not None:
            return existing, False
    if await session.get(Skill, skill_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Skill not found")
    now = datetime.now(UTC)
    award = SkillAward(
        student_id=student_id,
        skill_id=skill_id,
        level=level,
        awarded_on=awarded_on or now.date(),
        source=source,
        status=AwardStatus.VERIFIED,
        evidence_note=evidence_note,
        artefact_media_id=artefact_media_id,
        session_id=session_id,
        proposed_by_id=verified_by,
        verified_by_id=verified_by,
        verified_at=now,
        idempotency_key=idempotency_key,
    )
    session.add(award)
    await session.flush()
    await session.refresh(award, ["skill"])
    await emit(session, WebhookEvent.SKILL_VERIFIED, _hook(award))
    return award, True


def _hook(a: SkillAward) -> dict:
    return {
        "award_id": a.id,
        "student_id": a.student_id,
        "skill_code": a.skill.code if a.skill else None,
        "level": a.level,
        "source": a.source.value,
    }


async def propose_from_rubric(
    session: AsyncSession, result: Result, edition: CompetitionEdition
) -> list[SkillAward]:
    """Turn rubric criterion scores into PROPOSED awards for each participant."""
    if not edition.rubric or not result.rubric_scores:
        return []
    codes = [c.get("skill_code") for c in edition.rubric if c.get("skill_code")]
    skills = {s.code: s for s in (await session.scalars(select(Skill).where(Skill.code.in_(codes)))).all()}
    existing = {
        (a.student_id, a.rubric_criterion)
        for a in (await session.scalars(select(SkillAward).where(SkillAward.result_id == result.id))).all()
    }
    proposed = []
    for crit in edition.rubric:
        key, code, level = crit.get("key"), crit.get("skill_code"), crit.get("level_if_met")
        if not key or not code or not level or code not in skills or key not in result.rubric_scores:
            continue
        max_score = Decimal(str(crit.get("max_score") or 4))
        threshold = Decimal(str(crit.get("threshold") or "0.75"))
        score = Decimal(str(result.rubric_scores[key]))
        if max_score <= 0 or score / max_score < threshold:
            continue
        for p in result.participants:
            if (p.student_id, key) in existing:
                continue
            a = SkillAward(
                student_id=p.student_id,
                skill_id=skills[code].id,
                level=int(level),
                awarded_on=edition.event_ends,
                source=AwardSource.RUBRIC,
                status=AwardStatus.PROPOSED,
                result_id=result.id,
                rubric_criterion=key,
                evidence_note=f"{crit.get('label', key)}: scored {score} of {max_score} at {edition.name}",
                proposed_by_id=result.recorded_by_id,
            )
            session.add(a)
            proposed.append(a)
    await session.flush()
    return proposed


async def confirm(
    session: AsyncSession, award: SkillAward, user_id: uuid.UUID, level: int | None = None
) -> None:
    if award.status != AwardStatus.PROPOSED:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Award {award.id} is {award.status.value}, not proposed"
        )
    now = datetime.now(UTC)
    if level is not None:
        award.level = level
    award.status = AwardStatus.VERIFIED
    award.verified_by_id = user_id
    award.verified_at = now
    if award.source == AwardSource.SELF:
        award.countersigned_by_id = user_id
    await session.flush()
    await session.refresh(award, ["skill"])
    await emit(session, WebhookEvent.SKILL_VERIFIED, _hook(award))
