import csv
import io
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.deps import CtxDep
from app.models import (
    CommunicationOptOut,
    Guardian,
    Message,
    MessageDelivery,
    MessageTemplate,
    SquadMembership,
    Student,
    StudentGuardian,
)
from app.models.enums import (
    NEGATIVE_MESSAGE_TYPES,
    DeliveryStatus,
    EnrolmentStatus,
    MembershipStatus,
    MessageStatus,
    MessageType,
    TemplateStatus,
    WhatsAppTemplateStatus,
)
from app.permissions import Cap, ensure_squad_in_scope, ensure_student_in_scope, not_found
from app.schemas.common import ORM
from app.services.messaging import auto, lifecycle
from app.services.messaging.render import RenderError, validate_template, variables_in

router = APIRouter(tags=["messaging"])


# ---------------- Templates ----------------
class TemplateIn(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_]{3,80}$")
    message_type: MessageType
    subject_en: str
    body_en: str
    subject_ar: str
    body_ar: str
    whatsapp_template_name: str | None = None


class TemplateOut(ORM):
    id: uuid.UUID
    key: str
    message_type: MessageType
    version: int
    status: TemplateStatus
    subject_en: str
    body_en: str
    subject_ar: str
    body_ar: str
    variables: list[str]
    whatsapp_template_name: str | None
    whatsapp_status: WhatsAppTemplateStatus
    approved_by_id: uuid.UUID | None
    approved_at: datetime | None


@router.get("/message-templates", response_model=list[TemplateOut])
async def list_templates(ctx: CtxDep, include_retired: bool = False) -> list[TemplateOut]:
    ctx.require(Cap.DRAFT_MESSAGES)
    stmt = select(MessageTemplate).order_by(MessageTemplate.key, MessageTemplate.version.desc())
    if not include_retired:
        stmt = stmt.where(MessageTemplate.status != TemplateStatus.RETIRED)
    return [TemplateOut.model_validate(t) for t in (await ctx.session.scalars(stmt)).all()]


@router.post("/message-templates", response_model=TemplateOut, status_code=201)
async def create_template_version(body: TemplateIn, ctx: CtxDep) -> TemplateOut:
    """Creates a new DRAFT version. Approved versions are immutable."""
    ctx.require(Cap.APPROVE_TEMPLATES)
    try:
        validate_template(body.subject_en, body.body_en, body.subject_ar, body.body_ar)
    except RenderError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    current = await ctx.session.scalar(select(func.max(MessageTemplate.version)).where(MessageTemplate.key == body.key))
    t = MessageTemplate(
        **body.model_dump(),
        version=(current or 0) + 1,
        variables=variables_in(body.body_en, body.body_ar, body.subject_en, body.subject_ar),
        created_by_id=ctx.user_id,
    )
    ctx.session.add(t)
    await ctx.session.flush()
    ctx.audit("template.create_version", "message_template", t.id, context={"key": t.key, "version": t.version})
    return TemplateOut.model_validate(t)


@router.post("/message-templates/{template_id}/approve", response_model=TemplateOut)
async def approve_template(template_id: uuid.UUID, ctx: CtxDep) -> TemplateOut:
    ctx.require(Cap.APPROVE_TEMPLATES)
    t = await ctx.session.get(MessageTemplate, template_id)
    if t is None:
        raise not_found()
    if t.status != TemplateStatus.DRAFT:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Template is {t.status.value}")
    # Older approved versions of the same key are retired; drafts pinned to them keep working.
    for old in (await ctx.session.scalars(select(MessageTemplate).where(
            MessageTemplate.key == t.key, MessageTemplate.status == TemplateStatus.APPROVED))).all():
        old.status = TemplateStatus.RETIRED
    t.status, t.approved_by_id, t.approved_at = TemplateStatus.APPROVED, ctx.user_id, datetime.now(UTC)
    ctx.audit("template.approve", "message_template", t.id, context={"key": t.key, "version": t.version})
    return TemplateOut.model_validate(t)


class WhatsAppStatusIn(BaseModel):
    whatsapp_template_name: str
    whatsapp_status: WhatsAppTemplateStatus


@router.post("/message-templates/{template_id}/whatsapp", response_model=TemplateOut)
async def set_whatsapp_status(template_id: uuid.UUID, body: WhatsAppStatusIn, ctx: CtxDep) -> TemplateOut:
    """Record the Meta approval state of the WhatsApp template that mirrors this version."""
    ctx.require(Cap.APPROVE_TEMPLATES)
    t = await ctx.session.get(MessageTemplate, template_id)
    if t is None:
        raise not_found()
    t.whatsapp_template_name, t.whatsapp_status = body.whatsapp_template_name, body.whatsapp_status
    ctx.audit("template.whatsapp_status", "message_template", t.id, context=body.model_dump())
    return TemplateOut.model_validate(t)


# ---------------- Messages ----------------
class MessageOut(ORM):
    id: uuid.UUID
    message_type: MessageType
    template_id: uuid.UUID | None
    status: MessageStatus
    title: str
    student_ids: list[uuid.UUID]
    edition_id: uuid.UUID | None
    squad_id: uuid.UUID | None
    variables: dict
    created_by_id: uuid.UUID | None
    previewed_at: datetime | None
    released_by_id: uuid.UUID | None
    released_at: datetime | None
    scheduled_for: datetime | None
    is_emergency: bool
    emergency_reason: str | None
    created_at: datetime
    is_negative: bool = False


def message_out(m: Message) -> MessageOut:
    out = MessageOut.model_validate(m)
    out.is_negative = m.message_type in NEGATIVE_MESSAGE_TYPES
    return out


class DraftIn(BaseModel):
    message_type: MessageType
    template_id: uuid.UUID | None = None
    title: str
    student_ids: list[uuid.UUID] = []
    squad_id: uuid.UUID | None = None
    edition_id: uuid.UUID | None = None
    variables: dict[str, str] = {}


async def _scoped_students(ctx, body: DraftIn) -> list[uuid.UUID]:  # noqa: ANN001
    ids = list(body.student_ids)
    if body.squad_id:
        ensure_squad_in_scope(ctx.principal, body.squad_id)
        ids += list(await ctx.session.scalars(select(SquadMembership.student_id).where(
            SquadMembership.squad_id == body.squad_id, SquadMembership.status == MembershipStatus.ACTIVE)))
    ids = list(dict.fromkeys(ids))
    for sid in ids:
        await ensure_student_in_scope(ctx.session, ctx.principal, sid)
    return ids


@router.get("/messages", response_model=list[MessageOut])
async def list_messages(
    ctx: CtxDep, status_: MessageStatus | None = Query(None, alias="status"), message_type: MessageType | None = None
) -> list[MessageOut]:
    ctx.require(Cap.DRAFT_MESSAGES)
    stmt = select(Message).order_by(Message.created_at.desc()).limit(200)
    if not ctx.principal.can(Cap.RELEASE_MESSAGES):
        stmt = stmt.where(Message.created_by_id == ctx.user_id)
    if status_:
        stmt = stmt.where(Message.status == status_)
    if message_type:
        stmt = stmt.where(Message.message_type == message_type)
    return [message_out(m) for m in (await ctx.session.scalars(stmt)).all()]


@router.post("/messages/draft", status_code=201)
async def draft_message(body: DraftIn, ctx: CtxDep) -> dict:
    """Teachers, admins and leaders may draft. Opt-outs are shown to the drafter up front."""
    ctx.require(Cap.DRAFT_MESSAGES)
    if body.message_type == MessageType.EMERGENCY:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Use the emergency broadcast path")
    template_id = body.template_id
    if template_id is None:
        t = await auto.latest_approved_template(ctx.session, body.message_type)
        if t is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No approved template for this message type")
        template_id = t.id
    else:
        t = await ctx.session.get(MessageTemplate, template_id)
        if t is None or t.message_type != body.message_type:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Template does not match the message type")
    student_ids = await _scoped_students(ctx, body)
    m = Message(
        message_type=body.message_type, template_id=template_id, title=body.title, student_ids=student_ids,
        squad_id=body.squad_id, edition_id=body.edition_id, variables=body.variables, created_by_id=ctx.user_id,
    )
    ctx.session.add(m)
    await ctx.session.flush()
    guardian_ids = list(await ctx.session.scalars(
        select(StudentGuardian.guardian_id).where(StudentGuardian.student_id.in_(student_ids or [uuid.UUID(int=0)]))))
    from app.services.messaging.core import opted_out_count

    opted = await opted_out_count(ctx.session, guardian_ids, body.message_type)
    ctx.audit("message.draft", "message", m.id, context={"type": body.message_type.value, "students": len(student_ids)})
    return {"message": message_out(m), "families_opted_out_of_category": opted,
            "is_negative": body.message_type in NEGATIVE_MESSAGE_TYPES}


async def _message(ctx, message_id: uuid.UUID) -> Message:  # noqa: ANN001
    ctx.require(Cap.DRAFT_MESSAGES)
    m = await ctx.session.get(Message, message_id)
    if m is None:
        raise not_found("Message not found")
    if not ctx.principal.can(Cap.RELEASE_MESSAGES) and m.created_by_id != ctx.user_id:
        raise not_found("Message not found")
    return m


@router.get("/messages/{message_id}", response_model=MessageOut)
async def get_message(message_id: uuid.UUID, ctx: CtxDep) -> MessageOut:
    return message_out(await _message(ctx, message_id))


class MessagePatch(BaseModel):
    title: str | None = None
    student_ids: list[uuid.UUID] | None = None
    variables: dict[str, str] | None = None
    template_id: uuid.UUID | None = None


@router.patch("/messages/{message_id}", response_model=MessageOut)
async def update_message(message_id: uuid.UUID, body: MessagePatch, ctx: CtxDep) -> MessageOut:
    m = await _message(ctx, message_id)
    if m.status != MessageStatus.DRAFT:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only drafts can be edited")
    if body.student_ids is not None:
        for sid in body.student_ids:
            await ensure_student_in_scope(ctx.session, ctx.principal, sid)
        m.student_ids = list(dict.fromkeys(body.student_ids))
    if body.title is not None:
        m.title = body.title
    if body.variables is not None:
        m.variables = body.variables
    if body.template_id is not None:
        m.template_id = body.template_id
    m.previewed_at = None  # any edit requires a fresh preview
    ctx.audit("message.edit", "message", m.id)
    return message_out(m)


@router.post("/messages/{message_id}/preview")
async def preview_message(message_id: uuid.UUID, ctx: CtxDep) -> dict:
    """Render against three real recipients and summarise safeguards for the whole audience."""
    m = await _message(ctx, message_id)
    out = await lifecycle.preview(ctx.session, ctx.settings, ctx.tenant, m, datetime.now(UTC))
    ctx.audit("message.preview", "message", m.id, context={"families": out["family_count"]})
    return out


class ReleaseIn(BaseModel):
    scheduled_for: datetime | None = None


@router.post("/messages/{message_id}/release")
async def release_message(message_id: uuid.UUID, body: ReleaseIn, ctx: CtxDep) -> dict:
    """The human release. Requires the approver role; the approver is recorded by name."""
    ctx.require(Cap.RELEASE_MESSAGES)
    m = await _message(ctx, message_id)
    now = datetime.now(UTC)
    n = await lifecycle.release(ctx.session, ctx.tenant, m, ctx.user_id, now, body.scheduled_for)
    ctx.audit("message.release", "message", m.id, context={
        "families": n, "type": m.message_type.value, "scheduled_for": body.scheduled_for,
        "approver_name": ctx.principal.display_name})
    ctx.defer("dispatch_message", message_id=m.id)
    return {"message": message_out(m), "deliveries_created": n}


@router.post("/messages/{message_id}/handoff")
async def prepare_handoff(message_id: uuid.UUID, ctx: CtxDep) -> dict:
    """Negative news: render each family's draft for a member of staff to send personally."""
    m = await _message(ctx, message_id)
    n = await lifecycle.prepare_handoff(ctx.session, ctx.tenant, m, ctx.user_id)
    ctx.audit("message.handoff_prepared", "message", m.id, context={"families": n})
    return {"message": message_out(m), "drafts": n}


class PersonalSendIn(BaseModel):
    channel_note: str = Field(min_length=2, description="e.g. 'phoned 14:10', 'met at pick-up'")


@router.post("/messages/{message_id}/deliveries/{delivery_id}/sent-personally")
async def mark_sent_personally(message_id: uuid.UUID, delivery_id: uuid.UUID, body: PersonalSendIn, ctx: CtxDep) -> dict:
    m = await _message(ctx, message_id)
    if m.status != MessageStatus.MANUAL_HANDOFF:
        raise HTTPException(status.HTTP_409_CONFLICT, "Not a personal hand-off message")
    d = await ctx.session.get(MessageDelivery, delivery_id)
    if d is None or d.message_id != m.id:
        raise not_found()
    d.status, d.sent_at, d.error = DeliveryStatus.SENT, datetime.now(UTC), f"sent personally: {body.channel_note}"
    ctx.audit("message.sent_personally", "message", m.id, context={"delivery_id": d.id, "note": body.channel_note})
    return {"ok": True}


class CancelIn(BaseModel):
    reason: str = Field(min_length=3)


@router.post("/messages/{message_id}/cancel", response_model=MessageOut)
async def cancel_message(message_id: uuid.UUID, body: CancelIn, ctx: CtxDep) -> MessageOut:
    m = await _message(ctx, message_id)
    if m.status not in (MessageStatus.DRAFT, MessageStatus.RELEASED, MessageStatus.MANUAL_HANDOFF):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Message is {m.status.value}")
    if m.status == MessageStatus.RELEASED:
        ctx.require(Cap.RELEASE_MESSAGES)
    m.status, m.cancelled_reason = MessageStatus.CANCELLED, body.reason
    for d in (await ctx.session.scalars(select(MessageDelivery).where(
            MessageDelivery.message_id == m.id,
            MessageDelivery.status.in_([DeliveryStatus.PENDING, DeliveryStatus.HELD_QUIET_HOURS, DeliveryStatus.THROTTLED])))).all():
        d.status = DeliveryStatus.CANCELLED
    ctx.audit("message.cancel", "message", m.id, reason=body.reason)
    return message_out(m)


@router.get("/messages/{message_id}/delivery")
async def message_delivery(message_id: uuid.UUID, ctx: CtxDep) -> dict:
    """Full audit: approver, template version, channel, status and opens for every family."""
    m = await _message(ctx, message_id)
    rows = (
        await ctx.session.execute(
            select(MessageDelivery, Guardian.full_name)
            .join(Guardian, Guardian.id == MessageDelivery.guardian_id)
            .where(MessageDelivery.message_id == m.id)
            .order_by(Guardian.full_name)
        )
    ).all()
    counts: dict[str, int] = {}
    items = []
    for d, name in rows:
        counts[d.status.value] = counts.get(d.status.value, 0) + 1
        items.append({
            "delivery_id": d.id, "guardian_id": d.guardian_id, "guardian_name": name, "student_ids": d.student_ids,
            "language": d.language.value, "channel_planned": d.channel_planned.value,
            "channel_used": d.channel_used.value if d.channel_used else None, "channels_attempted": d.channels_attempted,
            "status": d.status.value, "template_version": d.template_version, "error": d.error,
            "hold_until": d.hold_until, "sent_at": d.sent_at, "delivered_at": d.delivered_at, "opened_at": d.opened_at,
            "subject": d.rendered_subject, "body": d.rendered_body,
        })
    return {"message": message_out(m), "released_by_id": m.released_by_id, "released_at": m.released_at,
            "counts": counts, "deliveries": items}


@router.get("/messages/{message_id}/delivery.csv")
async def message_delivery_csv(message_id: uuid.UUID, ctx: CtxDep) -> Response:
    data = await message_delivery(message_id, ctx)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["message_id", "released_by_id", "released_at", "guardian", "language", "template_version",
                "channel_used", "status", "sent_at", "delivered_at", "opened_at", "error"])
    for d in data["deliveries"]:
        w.writerow([message_id, data["released_by_id"], data["released_at"], d["guardian_name"], d["language"],
                    d["template_version"], d["channel_used"], d["status"], d["sent_at"], d["delivered_at"],
                    d["opened_at"], d["error"]])
    ctx.audit("message.delivery_export", "message", message_id)
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="delivery-{message_id}.csv"'})


# ---------------- Emergency broadcast ----------------
class EmergencyIn(BaseModel):
    reason: str = Field(min_length=10)
    title: str
    notice_en: str = Field(min_length=5)
    notice_ar: str = Field(min_length=5)
    squad_ids: list[uuid.UUID] = []
    edition_id: uuid.UUID | None = None
    whole_school: bool = False
    confirm: bool = False


@router.post("/messages/emergency")
async def emergency_broadcast(body: EmergencyIn, ctx: CtxDep) -> dict:
    """Separate, clearly-marked path. Leader only, always logged with a reason. Bypasses quiet
    hours and the weekly cap — nothing else. Call once to preview, again with confirm=true."""
    ctx.require(Cap.EMERGENCY_BROADCAST)
    template = await auto.latest_approved_template(ctx.session, MessageType.EMERGENCY)
    if template is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No approved emergency template")
    if body.whole_school:
        ids = list(await ctx.session.scalars(select(Student.id).where(Student.enrolment_status == EnrolmentStatus.ACTIVE)))
    else:
        ids = list(await ctx.session.scalars(select(SquadMembership.student_id).where(
            SquadMembership.squad_id.in_(body.squad_ids or [uuid.UUID(int=0)]),
            SquadMembership.status == MembershipStatus.ACTIVE)))
        if body.edition_id:
            from app.models import SquadTargetEdition
            squads = list(await ctx.session.scalars(select(SquadTargetEdition.squad_id).where(
                SquadTargetEdition.edition_id == body.edition_id)))
            ids += list(await ctx.session.scalars(select(SquadMembership.student_id).where(
                SquadMembership.squad_id.in_(squads or [uuid.UUID(int=0)]),
                SquadMembership.status == MembershipStatus.ACTIVE)))
    ids = list(dict.fromkeys(ids))
    if not ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No recipients selected")
    m = Message(message_type=MessageType.EMERGENCY, template_id=template.id, title=f"EMERGENCY: {body.title}",
                student_ids=ids, edition_id=body.edition_id, is_emergency=True, emergency_reason=body.reason,
                variables={"notice_en": body.notice_en, "notice_ar": body.notice_ar}, created_by_id=ctx.user_id)
    ctx.session.add(m)
    await ctx.session.flush()
    now = datetime.now(UTC)
    preview = await lifecycle.preview(ctx.session, ctx.settings, ctx.tenant, m, now)
    if not body.confirm:
        m.status = MessageStatus.CANCELLED
        m.cancelled_reason = "emergency preview only"
        ctx.audit("message.emergency_preview", "message", m.id, reason=body.reason)
        return {"preview": preview, "released": False}
    n = await lifecycle.release(ctx.session, ctx.tenant, m, ctx.user_id, now)
    ctx.audit("message.emergency_broadcast", "message", m.id, reason=body.reason,
              context={"families": n, "approver_name": ctx.principal.display_name})
    ctx.defer("dispatch_message", message_id=m.id)
    return {"preview": preview, "released": True, "message": message_out(m), "deliveries_created": n}


# ---------------- Auto-drafted batches ----------------
class EditionRef(BaseModel):
    edition_id: uuid.UUID


@router.post("/messages/result-batch", status_code=201)
async def result_batch(body: EditionRef, ctx: CtxDep) -> MessageOut:
    """Admin reviews the batch of result notifications for an edition, then releases it."""
    ctx.require(Cap.RELEASE_MESSAGES)
    m = await auto.draft_result_batch(ctx.session, body.edition_id, ctx.user_id)
    if m is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No results or no approved template")
    ctx.audit("message.result_batch_drafted", "message", m.id)
    return message_out(m)


@router.get("/messages-opt-outs")
async def opt_out_summary(ctx: CtxDep) -> dict:
    """Visible to admins before they draft: who has opted out of which category."""
    ctx.require(Cap.DRAFT_MESSAGES)
    rows = (
        await ctx.session.execute(
            select(CommunicationOptOut.category, func.count())
            .where(CommunicationOptOut.opted_back_in_at.is_(None))
            .group_by(CommunicationOptOut.category)
        )
    ).all()
    return {"by_category": {c.value: n for c, n in rows}}
