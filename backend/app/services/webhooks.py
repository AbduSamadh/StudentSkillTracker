"""Outbound webhooks: result.recorded, skill.verified, student.flagged (spec §9).

Payloads carry identifiers and outcome fields only — never names or contact details — and
are signed with HMAC-SHA256 so the receiver can verify origin.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import _jsonable
from app.db import tenant_session
from app.models import WebhookDelivery, WebhookSubscription
from app.models.enums import WebhookEvent
from app.security import decrypt_field, sign_payload

BACKOFF_MINUTES = [1, 5, 30, 120, 720]
MAX_ATTEMPTS = len(BACKOFF_MINUTES) + 1


async def emit(session: AsyncSession, event: WebhookEvent, payload: dict) -> int:
    subs = (
        await session.scalars(select(WebhookSubscription).where(WebhookSubscription.is_active.is_(True)))
    ).all()
    n = 0
    for sub in subs:
        if event.value in sub.events:
            session.add(
                WebhookDelivery(
                    subscription_id=sub.id,
                    event=event.value,
                    payload=_jsonable({"event": event.value, "occurred_at": datetime.now(UTC), "data": payload}),
                    next_attempt_at=datetime.now(UTC),
                )
            )
            n += 1
    return n


async def deliver_pending(tenant_id: str, **_: object) -> int:
    now = datetime.now(UTC)
    delivered = 0
    async with tenant_session(uuid.UUID(tenant_id)) as session:
        rows = (
            await session.execute(
                select(WebhookDelivery, WebhookSubscription)
                .join(WebhookSubscription, WebhookSubscription.id == WebhookDelivery.subscription_id)
                .where(WebhookDelivery.status == "pending", WebhookDelivery.next_attempt_at <= now)
                .limit(100)
            )
        ).all()
        async with httpx.AsyncClient(timeout=10) as client:
            for d, sub in rows:
                body = json.dumps(d.payload, separators=(",", ":")).encode()
                secret = decrypt_field(sub.secret_enc) or ""
                d.attempts += 1
                try:
                    r = await client.post(
                        sub.url,
                        content=body,
                        headers={
                            "Content-Type": "application/json",
                            "X-Stemtrack-Event": d.event,
                            "X-Stemtrack-Delivery": str(d.id),
                            "X-Stemtrack-Signature": f"sha256={sign_payload(secret, body)}",
                        },
                    )
                    d.last_response_code = r.status_code
                    ok = 200 <= r.status_code < 300
                    d.last_error = None if ok else r.text[:500]
                except httpx.HTTPError as exc:
                    ok = False
                    d.last_error = str(exc)[:500]
                if ok:
                    d.status, d.delivered_at = "delivered", now
                    delivered += 1
                elif d.attempts >= MAX_ATTEMPTS:
                    d.status = "failed"
                else:
                    d.next_attempt_at = now + timedelta(minutes=BACKOFF_MINUTES[d.attempts - 1])
        await session.commit()
    return delivered
