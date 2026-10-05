"""Channel adapters: WhatsApp Cloud API, email, SMS, in-app.

When a provider is not configured the adapter writes to the ``outbox`` table instead of
sending. Staging must run this way — it holds synthetic data only and must never reach
a real family.
"""

import asyncio
import logging
import smtplib
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import OutboxEntry, PortalNotification
from app.models.enums import Channel

log = logging.getLogger(__name__)
WHATSAPP_GRAPH_URL = "https://graph.facebook.com/v21.0"


@dataclass
class SendResult:
    ok: bool
    provider_message_id: str | None = None
    error: str | None = None
    # WhatsApp: true when the failure means "use another channel" (no template, 24h window, number not on WA).
    fallback_recommended: bool = True


async def _outbox(
    session: AsyncSession, channel: Channel, recipient: str, subject: str | None, body: str, meta: dict
) -> SendResult:
    entry = OutboxEntry(channel=channel, recipient=recipient, subject=subject, body=body, meta=meta)
    session.add(entry)
    await session.flush()
    log.info("outbox %s -> %s: %s", channel.value, recipient, subject or body[:60])
    return SendResult(ok=True, provider_message_id=f"outbox:{entry.id}")


async def send_email(
    session: AsyncSession, to: str, subject: str, body: str, *, meta: dict[str, Any] | None = None
) -> SendResult:
    settings = get_settings()
    meta = meta or {}
    if settings.email_provider == "outbox":
        return await _outbox(session, Channel.EMAIL, to, subject, body, meta)
    try:
        if settings.email_provider == "postmark":
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    "https://api.postmarkapp.com/email",
                    headers={
                        "X-Postmark-Server-Token": settings.postmark_token or "",
                        "Accept": "application/json",
                    },
                    json={
                        "From": settings.email_from,
                        "To": to,
                        "Subject": subject,
                        "TextBody": body,
                        "MessageStream": "outbound",
                        "Metadata": {k: str(v) for k, v in meta.items()},
                    },
                )
            if r.status_code >= 400:
                return SendResult(ok=False, error=f"postmark {r.status_code}: {r.text[:200]}")
            return SendResult(ok=True, provider_message_id=r.json().get("MessageID"))
        if settings.email_provider == "smtp":
            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = settings.email_from, to, subject
            msg.set_content(body)

            def _send() -> None:
                with smtplib.SMTP(settings.smtp_host or "localhost", settings.smtp_port, timeout=10) as s:
                    s.starttls()
                    s.send_message(msg)

            await asyncio.to_thread(_send)
            return SendResult(ok=True, provider_message_id=f"smtp:{uuid.uuid4().hex}")
        return SendResult(ok=False, error=f"email provider {settings.email_provider} not implemented")
    except (httpx.HTTPError, OSError, smtplib.SMTPException) as exc:
        return SendResult(ok=False, error=str(exc)[:500])


async def send_sms(session: AsyncSession, to: str, body: str, *, meta: dict | None = None) -> SendResult:
    settings = get_settings()
    if settings.sms_provider == "outbox":
        return await _outbox(session, Channel.SMS, to, None, body, meta or {})
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json",
                auth=(settings.twilio_account_sid or "", settings.twilio_auth_token or ""),
                data={"From": settings.twilio_from or "", "To": to, "Body": body},
            )
        if r.status_code >= 400:
            return SendResult(ok=False, error=f"twilio {r.status_code}: {r.text[:200]}")
        return SendResult(ok=True, provider_message_id=r.json().get("sid"))
    except httpx.HTTPError as exc:
        return SendResult(ok=False, error=str(exc)[:500])


async def send_whatsapp_template(
    session: AsyncSession,
    to: str,
    *,
    template_name: str | None,
    language: str,
    parameters: list[str],
    preview_body: str,
    meta: dict | None = None,
) -> SendResult:
    """Business-initiated WhatsApp messages must use a Meta-approved template."""
    settings = get_settings()
    if not template_name:
        return SendResult(ok=False, error="no approved WhatsApp template for this message type")
    if settings.whatsapp_provider == "outbox":
        return await _outbox(
            session,
            Channel.WHATSAPP,
            to,
            template_name,
            preview_body,
            {**(meta or {}), "template": template_name, "language": language, "parameters": parameters},
        )
    payload = {
        "messaging_product": "whatsapp",
        "to": to.lstrip("+"),
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": "ar" if language == "ar" else "en"},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": p} for p in parameters]}],
        },
    }
    if meta and meta.get("tenant_id") and meta.get("delivery_id"):
        # Returned on status webhooks so the receiver can set the tenant context.
        payload["biz_opaque_callback_data"] = f"{meta['tenant_id']}:{meta['delivery_id']}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{WHATSAPP_GRAPH_URL}/{settings.whatsapp_phone_number_id}/messages",
                headers={"Authorization": f"Bearer {settings.whatsapp_access_token}"},
                json=payload,
            )
        if r.status_code >= 400:
            return SendResult(ok=False, error=f"whatsapp {r.status_code}: {r.text[:200]}")
        messages = r.json().get("messages") or [{}]
        return SendResult(ok=True, provider_message_id=messages[0].get("id"))
    except httpx.HTTPError as exc:
        return SendResult(ok=False, error=str(exc)[:500])


async def send_in_app(
    session: AsyncSession, guardian_id: uuid.UUID, title: str, body: str, delivery_id: uuid.UUID | None
) -> SendResult:
    note = PortalNotification(guardian_id=guardian_id, title=title, body=body, delivery_id=delivery_id)
    session.add(note)
    await session.flush()
    return SendResult(ok=True, provider_message_id=f"in_app:{note.id}")
