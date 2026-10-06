"""Provider delivery webhooks are unauthenticated endpoints, so each one must prove it came from the
provider before it can change a delivery's status (which is part of the messaging audit trail)."""

import base64
import hashlib
import hmac
import json
import uuid
from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.config import Settings, get_settings
from app.db import tenant_session
from app.models import Guardian, Message, MessageDelivery
from app.models.enums import Channel, DeliveryStatus, Language, MessageType
from tests.conftest import World


@pytest.fixture
def secrets() -> Iterator[Settings]:
    s = get_settings()
    saved = (s.whatsapp_app_secret, s.postmark_webhook_password)
    s.whatsapp_app_secret, s.postmark_webhook_password = "wa-app-secret", "pm-hook-password"
    yield s
    s.whatsapp_app_secret, s.postmark_webhook_password = saved


async def _sent_delivery(world: World, channel: Channel) -> uuid.UUID:
    async with tenant_session(world.tenant) as s:
        guardian = await s.scalar(select(Guardian).limit(1))
        assert guardian is not None
        m = Message(message_type=MessageType.LOGISTICS, title="webhook test")
        s.add(m)
        await s.flush()
        d = MessageDelivery(
            message_id=m.id,
            guardian_id=guardian.id,
            language=Language.EN,
            channel_planned=channel,
            channel_used=channel,
            status=DeliveryStatus.SENT,
        )
        s.add(d)
        await s.commit()
        return d.id


async def _status(world: World, delivery_id: uuid.UUID) -> DeliveryStatus:
    async with tenant_session(world.tenant) as s:
        d = await s.get(MessageDelivery, delivery_id)
        assert d is not None
        return d.status


async def test_whatsapp_status_needs_a_valid_signature(client, world: World, secrets: Settings) -> None:  # noqa: ANN001
    did = await _sent_delivery(world, Channel.WHATSAPP)
    body = json.dumps(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "statuses": [
                                    {
                                        "status": "delivered",
                                        "timestamp": "1790000000",
                                        "biz_opaque_callback_data": f"{world.tenant}:{did}",
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
    ).encode()
    headers = {"content-type": "application/json"}
    forged = await client.post("/api/v1/webhooks/whatsapp", content=body, headers=headers)
    assert forged.status_code == 401
    bad = await client.post(
        "/api/v1/webhooks/whatsapp", content=body, headers={**headers, "x-hub-signature-256": "sha256=00"}
    )
    assert bad.status_code == 401
    assert await _status(world, did) == DeliveryStatus.SENT

    sig = "sha256=" + hmac.new(b"wa-app-secret", body, hashlib.sha256).hexdigest()
    ok = await client.post(
        "/api/v1/webhooks/whatsapp", content=body, headers={**headers, "x-hub-signature-256": sig}
    )
    assert ok.status_code == 200 and ok.json() == {"processed": 1}
    assert await _status(world, did) == DeliveryStatus.DELIVERED


async def test_postmark_event_needs_the_webhook_password(client, world: World, secrets: Settings) -> None:  # noqa: ANN001
    did = await _sent_delivery(world, Channel.EMAIL)
    event = {"RecordType": "Delivery", "Metadata": {"tenant_id": str(world.tenant), "delivery_id": str(did)}}
    assert (await client.post("/api/v1/webhooks/email/postmark", json=event)).status_code == 401
    wrong = base64.b64encode(b"postmark:guess").decode()
    r = await client.post(
        "/api/v1/webhooks/email/postmark", json=event, headers={"authorization": f"Basic {wrong}"}
    )
    assert r.status_code == 401
    assert await _status(world, did) == DeliveryStatus.SENT

    right = base64.b64encode(b"postmark:pm-hook-password").decode()
    r = await client.post(
        "/api/v1/webhooks/email/postmark", json=event, headers={"authorization": f"Basic {right}"}
    )
    assert r.status_code == 200
    assert await _status(world, did) == DeliveryStatus.DELIVERED


def test_production_refuses_to_start_without_webhook_secrets() -> None:
    base = {
        "environment": "production",
        "jwt_secret": "x" * 48,
        "blind_index_key": "y" * 32,
        "field_encryption_key": Fernet.generate_key().decode(),
    }
    Settings(**base).assert_safe_for_production()
    with pytest.raises(RuntimeError, match="whatsapp_app_secret"):
        Settings(**base, whatsapp_provider="cloud_api").assert_safe_for_production()
    with pytest.raises(RuntimeError, match="postmark_webhook_password"):
        Settings(**base, email_provider="postmark").assert_safe_for_production()
    Settings(
        **base,
        email_provider="postmark",
        postmark_webhook_password="p",
        whatsapp_provider="cloud_api",
        whatsapp_app_secret="s",
    ).assert_safe_for_production()
