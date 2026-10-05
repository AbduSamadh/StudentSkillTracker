"""Draft -> preview -> human release -> dispatch."""

import secrets
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConsentRequest, Message, MessageDelivery, MessageTemplate, Tenant
from app.models.enums import (
    NEGATIVE_MESSAGE_TYPES,
    ConsentPurpose,
    DeliveryStatus,
    Language,
    MessageStatus,
    MessageType,
    TemplateStatus,
)
from app.services.messaging.core import (
    Family,
    build_context,
    channel_order,
    evaluate,
    opted_out_count,
    render_for,
    resolve_families,
)
from app.services.messaging.render import RenderError
from app.tenancy import TenantSettings

PREVIEW_SAMPLE = 3

CATEGORY_LABELS = {
    MessageType.SELECTION_NOTICE: ("selection notices", "إشعارات الاختيار"),
    MessageType.LOGISTICS: ("event logistics", "الترتيبات اللوجستية للفعاليات"),
    MessageType.CONSENT_REQUEST: ("consent requests", "طلبات الموافقة"),
    MessageType.RESULT_NOTIFICATION: ("result notifications", "إشعارات النتائج"),
    MessageType.PROGRESS_REPORT: ("progress reports", "تقارير التقدّم"),
    MessageType.CELEBRATION: ("celebrations", "التهاني"),
}


def with_opt_out_footer(body: str, message: Message, ctx: dict, lang: Language) -> str:
    """One-tap opt-out per category on every message except emergencies."""
    if message.message_type == MessageType.EMERGENCY or ctx["opt_out_link"] in body:
        return body
    en, ar = CATEGORY_LABELS.get(
        message.message_type, (message.message_type.value, message.message_type.value)
    )
    if lang == Language.AR:
        return f"{body}\n\nلإيقاف رسائل {ar}: {ctx['opt_out_link']}"
    return f"{body}\n\nTo stop receiving {en}: {ctx['opt_out_link']}"


async def template_for(session: AsyncSession, message: Message) -> MessageTemplate:
    if message.template_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Message has no template")
    t = await session.get(MessageTemplate, message.template_id)
    if t is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Template not found")
    return t


async def preview(
    session: AsyncSession, settings: TenantSettings, tenant: Tenant, message: Message, now: datetime
) -> dict:
    """Render exactly what three real recipients would receive, and show every safeguard
    that would apply to the whole audience *before* anyone presses release."""
    template = await template_for(session, message)
    families = await resolve_families(session, message)
    samples = []
    errors = []
    for fam in families[:PREVIEW_SAMPLE]:
        lang = fam.guardian.language
        try:
            ctx = await build_context(session, tenant, message, fam, lang)
            subject, body = render_for(template, lang, ctx)
            body = with_opt_out_footer(body, message, ctx, lang)
        except RenderError as exc:
            errors.append(str(exc))
            subject, body = None, None
        verdict = await evaluate(
            session, settings, message, fam.guardian.id, [s.id for s in fam.students], now
        )
        samples.append(
            {
                "guardian_id": fam.guardian.id,
                "guardian_name": fam.guardian.full_name,
                "children": [s.display_name for s in fam.students],
                "language": lang.value,
                "channels": [c.value for c in channel_order(fam.guardian, template)],
                "subject": subject,
                "body": body,
                "would_be": verdict.status.value,
                "reason": verdict.reason,
                "sent_this_week": verdict.sent_this_week,
                "weekly_cap": settings.weekly_message_cap,
            }
        )
    summary = {s.value: 0 for s in DeliveryStatus}
    near_cap = 0
    for fam in families:
        v = await evaluate(session, settings, message, fam.guardian.id, [s.id for s in fam.students], now)
        summary[v.status.value] += 1
        if v.sent_this_week == settings.weekly_message_cap - 1 and not message.is_emergency:
            near_cap += 1
    opted = await opted_out_count(session, [f.guardian.id for f in families], message.message_type)
    message.previewed_at = now
    return {
        "message_id": message.id,
        "message_type": message.message_type.value,
        "is_negative": message.message_type in NEGATIVE_MESSAGE_TYPES,
        "template_key": template.key,
        "template_version": template.version,
        "template_approved": template.status == TemplateStatus.APPROVED,
        "student_count": len(message.student_ids),
        "family_count": len(families),
        "samples": samples,
        "render_errors": errors,
        "audience_status": {k: v for k, v in summary.items() if v},
        "families_opted_out": opted,
        "families_one_below_cap": near_cap,
        "weekly_cap": settings.weekly_message_cap,
        "quiet_hours": {
            "start": settings.quiet_hours.start.isoformat(),
            "end": settings.quiet_hours.end.isoformat(),
        },
    }


async def _materialise(
    session: AsyncSession, tenant: Tenant, message: Message, template: MessageTemplate, families: list[Family]
) -> int:
    """Snapshot what each family will receive at the moment of release."""
    existing = set(
        await session.scalars(
            select(MessageDelivery.guardian_id).where(MessageDelivery.message_id == message.id)
        )
    )
    n = 0
    for fam in families:
        if fam.guardian.id in existing:
            continue
        lang = fam.guardian.language
        ctx = await build_context(session, tenant, message, fam, lang)
        subject, body = render_for(template, lang, ctx)
        body = with_opt_out_footer(body, message, ctx, lang)
        channels = channel_order(fam.guardian, template)
        session.add(
            MessageDelivery(
                message_id=message.id,
                guardian_id=fam.guardian.id,
                student_ids=[s.id for s in fam.students],
                language=lang,
                channel_planned=channels[0],
                status=DeliveryStatus.PENDING,
                template_version=template.version,
                rendered_subject=subject,
                rendered_body=body,
                open_token=secrets.token_urlsafe(24),
            )
        )
        if message.message_type == MessageType.CONSENT_REQUEST:
            purpose = ConsentPurpose(
                (message.variables or {}).get("consent_purpose", ConsentPurpose.TRAVEL.value)
            )
            for st in fam.students:
                session.add(
                    ConsentRequest(
                        message_id=message.id,
                        student_id=st.id,
                        guardian_id=fam.guardian.id,
                        purpose=purpose,
                        edition_id=message.edition_id,
                    )
                )
        n += 1
    await session.flush()
    return n


async def release(
    session: AsyncSession,
    tenant: Tenant,
    message: Message,
    released_by: uuid.UUID,
    now: datetime,
    scheduled_for: datetime | None = None,
) -> int:
    if message.message_type in NEGATIVE_MESSAGE_TYPES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Negative news is never sent by the platform. Use 'Prepare for personal sending' instead.",
        )
    if message.status != MessageStatus.DRAFT:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Message is {message.status.value}")
    if message.previewed_at is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Preview the message with real recipients before release"
        )
    template = await template_for(session, message)
    if template.status != TemplateStatus.APPROVED:
        raise HTTPException(status.HTTP_409_CONFLICT, "The template version has not been approved")
    families = await resolve_families(session, message)
    if not families:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No recipients")
    try:
        n = await _materialise(session, tenant, message, template, families)
    except RenderError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    message.status = MessageStatus.RELEASED
    message.released_by_id = released_by
    message.released_at = now
    message.scheduled_for = scheduled_for
    return n


async def prepare_handoff(session: AsyncSession, tenant: Tenant, message: Message, user_id: uuid.UUID) -> int:
    """Negative-news path: render per-family drafts for a member of staff to send personally.
    These deliveries are never dispatched by the platform."""
    if message.message_type not in NEGATIVE_MESSAGE_TYPES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Only negative-news messages use personal hand-off"
        )
    template = await template_for(session, message)
    families = await resolve_families(session, message)
    try:
        n = await _materialise(session, tenant, message, template, families)
    except RenderError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    message.status = MessageStatus.MANUAL_HANDOFF
    return n


def can_dispatch(message: Message) -> bool:
    """The single gate every send passes through. No configuration bypasses it."""
    return (
        message.status == MessageStatus.RELEASED
        and message.released_by_id is not None
        and message.message_type not in NEGATIVE_MESSAGE_TYPES
    )


async def evaluate_delivery(
    session: AsyncSession, settings: TenantSettings, message: Message, d: MessageDelivery, now: datetime
):  # noqa: ANN201
    return await evaluate(session, settings, message, d.guardian_id, list(d.student_ids), now)


def utcnow() -> datetime:
    return datetime.now(UTC)
