import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.deps import CtxDep
from app.models import Consent, ConsentForm, Student
from app.models.enums import ConsentDecision, ConsentPurpose
from app.permissions import Cap, ensure_student_in_scope, not_found
from app.schemas.common import ORM
from app.services.consent import has_consent, latest

router = APIRouter(tags=["consent"])


class StaffConsentIn(BaseModel):
    student_id: uuid.UUID
    guardian_id: uuid.UUID | None = None
    purpose: ConsentPurpose
    decision: ConsentDecision | None = None  # None + withdraw=True for a withdrawal
    withdraw: bool = False
    edition_id: uuid.UUID | None = None
    method: str = Field(default="paper", pattern=r"^(paper|email|phone|mis_import)$")
    notes: str | None = None


class ConsentOut(ORM):
    id: uuid.UUID
    student_id: uuid.UUID
    guardian_id: uuid.UUID | None
    purpose: ConsentPurpose
    version: int
    edition_id: uuid.UUID | None
    decision: ConsentDecision
    decided_at: datetime
    withdrawn_at: datetime | None
    method: str
    recorded_by_id: uuid.UUID | None
    notes: str | None


@router.post("/consents", status_code=201)
async def record_consent(body: StaffConsentIn, ctx: CtxDep) -> dict:
    """Record a consent decision received outside the portal, or a withdrawal."""
    ctx.require(Cap.MANAGE_CONSENT)
    student = await ctx.session.get(Student, body.student_id)
    if student is None:
        raise not_found()
    now = datetime.now(UTC)
    if body.withdraw:
        c = await latest(ctx.session, student.id, body.purpose, body.edition_id)
        if c is None or c.withdrawn_at is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "No active consent to withdraw")
        c.withdrawn_at = now
        c.notes = ((c.notes or "") + f"\nWithdrawn via {body.method}: {body.notes or ''}").strip()
        ctx.audit("consent.withdraw", "student", student.id, context={"purpose": body.purpose.value, "method": body.method})
        return {"id": c.id, "withdrawn_at": now}
    if body.decision is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "decision is required")
    form = await ctx.session.scalar(select(ConsentForm).where(ConsentForm.purpose == body.purpose,
                                                              ConsentForm.is_current.is_(True)))
    c = Consent(student_id=student.id, guardian_id=body.guardian_id, purpose=body.purpose, edition_id=body.edition_id,
                consent_form_id=form.id if form else None, version=form.version if form else 1,
                decision=body.decision, decided_at=now, method=body.method, recorded_by_id=ctx.user_id,
                notes=body.notes)
    ctx.session.add(c)
    await ctx.session.flush()
    ctx.audit("consent.record", "student", student.id,
              context={"purpose": body.purpose.value, "decision": body.decision.value, "method": body.method})
    return {"id": c.id, "decision": c.decision.value}


@router.get("/students/{student_id}/consents")
async def student_consents(student_id: uuid.UUID, ctx: CtxDep) -> dict:
    ctx.require(Cap.VIEW_ROSTER)
    await ensure_student_in_scope(ctx.session, ctx.principal, student_id)
    s = await ctx.session.get(Student, student_id)
    if s is None:
        raise not_found()
    history = (await ctx.session.scalars(
        select(Consent).where(Consent.student_id == s.id).order_by(Consent.decided_at.desc()))).all()
    ctx.audit("student.consents_read", "student", s.id)
    return {
        "current": {p.value: await has_consent(ctx.session, s, p) for p in ConsentPurpose},
        "history": [ConsentOut.model_validate(c) for c in history],
    }
