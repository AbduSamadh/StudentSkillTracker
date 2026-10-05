"""Unauthenticated inbound endpoints: provider delivery webhooks and the squad ICS feed.

Each one carries the tenant identifier inside data we issued (callback metadata or an
opaque token), so the tenant context is set before any lookup and RLS still applies.
"""

import hashlib
import hmac
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from sqlalchemy import select

from app.config import get_settings
from app.db import tenant_session
from app.models import CompetitionEdition, MessageDelivery, Squad, SquadTargetEdition, TrainingSession
from app.models.enums import DeliveryStatus
from app.security import split_opaque_token

router = APIRouter(tags=["inbound"])

WA_STATUS = {
    "sent": DeliveryStatus.SENT,
    "delivered": DeliveryStatus.DELIVERED,
    "read": DeliveryStatus.READ,
    "failed": DeliveryStatus.FAILED,
}
RANK = {DeliveryStatus.SENT: 1, DeliveryStatus.DELIVERED: 2, DeliveryStatus.READ: 3}


def _parse_callback(value: str | None) -> tuple[uuid.UUID, uuid.UUID] | None:
    try:
        tenant, delivery = (value or "").split(":", 1)
        return uuid.UUID(tenant), uuid.UUID(delivery)
    except ValueError:
        return None


async def _apply_status(
    tenant_id: uuid.UUID, delivery_id: uuid.UUID, new: DeliveryStatus, at: datetime, error: str | None = None
) -> None:
    async with tenant_session(tenant_id) as session:
        d = await session.get(MessageDelivery, delivery_id)
        if d is None:
            return
        if new == DeliveryStatus.FAILED:
            # A failed WhatsApp send is retried through the fallback chain by the dispatcher.
            d.status, d.error = (
                DeliveryStatus.PENDING
                if d.channel_used and d.channel_used.value == "whatsapp"
                else DeliveryStatus.FAILED,
                error,
            )
        elif RANK.get(new, 0) > RANK.get(d.status, 0):
            d.status = new
            if new == DeliveryStatus.DELIVERED:
                d.delivered_at = at
            if new == DeliveryStatus.READ:
                d.delivered_at = d.delivered_at or at
                d.opened_at = d.opened_at or at
        await session.commit()


@router.get("/webhooks/whatsapp")
async def whatsapp_verify(
    mode: str = Query(alias="hub.mode"),
    token: str = Query(alias="hub.verify_token"),
    challenge: str = Query(alias="hub.challenge"),
) -> Response:
    if mode == "subscribe" and hmac.compare_digest(token, get_settings().whatsapp_verify_token):
        return Response(challenge, media_type="text/plain")
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Verification failed")


@router.post("/webhooks/whatsapp")
async def whatsapp_status(request: Request) -> dict:
    raw = await request.body()
    secret = get_settings().whatsapp_app_secret
    if secret:
        expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, request.headers.get("x-hub-signature-256", "")):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad signature")
    payload = await request.json()
    n = 0
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for st in change.get("value", {}).get("statuses", []):
                parsed = _parse_callback(st.get("biz_opaque_callback_data"))
                new = WA_STATUS.get(st.get("status", ""))
                if parsed and new:
                    at = datetime.fromtimestamp(int(st.get("timestamp", "0")), UTC)
                    err = (st.get("errors") or [{}])[0].get("title")
                    await _apply_status(parsed[0], parsed[1], new, at, err)
                    n += 1
    return {"processed": n}


@router.post("/webhooks/email/postmark")
async def postmark_event(request: Request) -> dict:
    payload = await request.json()
    meta = payload.get("Metadata") or {}
    parsed = _parse_callback(f"{meta.get('tenant_id', '')}:{meta.get('delivery_id', '')}")
    if not parsed:
        return {"processed": 0}
    kind = payload.get("RecordType")
    now = datetime.now(UTC)
    if kind == "Delivery":
        await _apply_status(*parsed, DeliveryStatus.DELIVERED, now)
    elif kind == "Open":
        await _apply_status(*parsed, DeliveryStatus.READ, now)
    elif kind == "Bounce":
        await _apply_status(*parsed, DeliveryStatus.FAILED, now, payload.get("Description"))
    return {"processed": 1}


def _ics_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


@router.get("/calendar/{token}.ics")
async def squad_calendar(token: str) -> Response:
    """Read-only ICS per squad: competition dates and training sessions. No student data."""
    parsed = split_opaque_token(token)
    if parsed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    tenant_id, raw = parsed
    async with tenant_session(tenant_id) as session:
        squad = await session.scalar(select(Squad).where(Squad.ics_token == raw))
        if squad is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        editions = (
            await session.scalars(
                select(CompetitionEdition)
                .join(SquadTargetEdition, SquadTargetEdition.edition_id == CompetitionEdition.id)
                .where(SquadTargetEdition.squad_id == squad.id)
            )
        ).all()
        sessions = (
            await session.scalars(
                select(TrainingSession).where(
                    TrainingSession.squad_id == squad.id,
                    TrainingSession.starts_at >= datetime.now(UTC) - timedelta(days=30),
                )
            )
        ).all()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//stemtrack//squad calendar//EN",
        f"X-WR-CALNAME:{_ics_escape(squad.name)}",
    ]
    for e in editions:
        lines += [
            "BEGIN:VEVENT",
            f"UID:edition-{e.id}@stemtrack",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{e.event_starts:%Y%m%d}",
            f"DTEND;VALUE=DATE:{(e.event_ends + timedelta(days=1)):%Y%m%d}",
            f"SUMMARY:{_ics_escape(e.name)}",
            f"LOCATION:{_ics_escape(e.venue or '')}",
            "END:VEVENT",
        ]
    for s in sessions:
        end = s.ends_at or s.starts_at + timedelta(hours=1)
        lines += [
            "BEGIN:VEVENT",
            f"UID:session-{s.id}@stemtrack",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{s.starts_at.astimezone(UTC):%Y%m%dT%H%M%SZ}",
            f"DTEND:{end.astimezone(UTC):%Y%m%dT%H%M%SZ}",
            f"SUMMARY:{_ics_escape('Training: ' + squad.name)}",
            f"LOCATION:{_ics_escape(s.location or '')}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return Response("\r\n".join(lines) + "\r\n", media_type="text/calendar")
