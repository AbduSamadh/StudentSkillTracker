"""Dispatch released messages. Runs after release, and every minute from the worker to pick
up deliveries whose quiet-hours hold or weekly-cap throttle has expired."""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import tenant_session
from app.models import Guardian, Message, MessageDelivery, Student, Tenant
from app.models.enums import Channel, DeliveryStatus, MessageStatus
from app.services import channels as ch
from app.services.messaging.core import Family, address_for, build_context, channel_order
from app.services.messaging.lifecycle import can_dispatch, evaluate_delivery, template_for
from app.tenancy import load_settings

log = logging.getLogger(__name__)
RETRYABLE = (DeliveryStatus.PENDING, DeliveryStatus.HELD_QUIET_HOURS, DeliveryStatus.THROTTLED)
TERMINAL = (
    DeliveryStatus.SENT, DeliveryStatus.DELIVERED, DeliveryStatus.READ, DeliveryStatus.FAILED,
    DeliveryStatus.BLOCKED_CONSENT, DeliveryStatus.BLOCKED_OPT_OUT, DeliveryStatus.CANCELLED,
)


async def _send_one(session: AsyncSession, tenant: Tenant, message: Message, d: MessageDelivery, now: datetime) -> None:
    template = await template_for(session, message)
    guardian = await session.get(Guardian, d.guardian_id)
    assert guardian is not None
    attempted: list[str] = []
    last_error = None
    for channel in channel_order(guardian, template):
        attempted.append(channel.value)
        meta = {"tenant_id": str(tenant.id), "message_id": str(message.id), "delivery_id": str(d.id)}
        if channel == Channel.IN_APP:
            res = await ch.send_in_app(session, guardian.id, d.rendered_subject or "", d.rendered_body or "", d.id)
        elif channel == Channel.EMAIL:
            res = await ch.send_email(session, address_for(guardian, channel) or "", d.rendered_subject or "",
                                      d.rendered_body or "", meta=meta)
        elif channel == Channel.SMS:
            res = await ch.send_sms(session, address_for(guardian, channel) or "", d.rendered_body or "", meta=meta)
        else:
            students = list((await session.scalars(select(Student).where(Student.id.in_(d.student_ids)))).all())
            ctx = await build_context(session, tenant, message, Family(guardian, students), d.language)
            params = [str(ctx.get(v, "")) for v in template.variables]
            res = await ch.send_whatsapp_template(
                session, address_for(guardian, channel) or "", template_name=template.whatsapp_template_name,
                language=d.language.value, parameters=params, preview_body=d.rendered_body or "", meta=meta,
            )
        if res.ok:
            d.status, d.channel_used, d.sent_at = DeliveryStatus.SENT, channel, now
            d.provider_message_id, d.error = res.provider_message_id, None
            break
        last_error = f"{channel.value}: {res.error}"
        log.warning("delivery %s via %s failed: %s", d.id, channel.value, res.error)
    else:
        d.status, d.error = DeliveryStatus.FAILED, last_error
    d.channels_attempted = attempted
    d.attempts += 1


async def dispatch_message(session: AsyncSession, tenant: Tenant, message: Message, now: datetime) -> dict:
    counts: dict[str, int] = {}
    if not can_dispatch(message):
        return {"dispatched": False, "reason": "not released by a named approver"}
    if message.scheduled_for and message.scheduled_for > now:
        return {"dispatched": False, "reason": "scheduled for later"}
    settings = load_settings(tenant.settings)
    deliveries = (
        await session.scalars(
            select(MessageDelivery).where(
                MessageDelivery.message_id == message.id,
                MessageDelivery.status.in_(RETRYABLE),
                or_(MessageDelivery.hold_until.is_(None), MessageDelivery.hold_until <= now),
            )
        )
    ).all()
    for d in deliveries:
        verdict = await evaluate_delivery(session, settings, message, d, now)
        if verdict.status == DeliveryStatus.PENDING:
            await _send_one(session, tenant, message, d, now)
            d.hold_until = None
        else:
            d.status, d.error, d.hold_until = verdict.status, verdict.reason, verdict.hold_until
        counts[d.status.value] = counts.get(d.status.value, 0) + 1
    remaining = await session.scalar(
        select(MessageDelivery.id).where(
            MessageDelivery.message_id == message.id, MessageDelivery.status.not_in(TERMINAL)
        ).limit(1)
    )
    if remaining is None:
        message.status = MessageStatus.COMPLETED
    await session.flush()
    return {"dispatched": True, "counts": counts}


async def dispatch_message_job(tenant_id: str, message_id: str, **_: object) -> dict:
    async with tenant_session(uuid.UUID(tenant_id)) as session:
        tenant = await session.get(Tenant, uuid.UUID(tenant_id))
        message = await session.get(Message, uuid.UUID(message_id))
        if tenant is None or message is None:
            return {"dispatched": False}
        out = await dispatch_message(session, tenant, message, datetime.now(UTC))
        await session.commit()
        return out


async def dispatch_due(tenant_id: str, **_: object) -> int:
    """Worker cron: every released message with deliveries that are now due."""
    now = datetime.now(UTC)
    async with tenant_session(uuid.UUID(tenant_id)) as session:
        tenant = await session.get(Tenant, uuid.UUID(tenant_id))
        assert tenant is not None
        ids = list(
            await session.scalars(
                select(Message.id).where(
                    Message.status == MessageStatus.RELEASED,
                    or_(Message.scheduled_for.is_(None), Message.scheduled_for <= now),
                )
            )
        )
        for mid in ids:
            message = await session.get(Message, mid)
            if message is not None:
                await dispatch_message(session, tenant, message, now)
        await session.commit()
        return len(ids)
