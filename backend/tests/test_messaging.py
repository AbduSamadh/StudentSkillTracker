"""Parent-messaging safeguards (spec §7.3; acceptance criteria 13.1.5 and 13.1.6)."""

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.db import tenant_session
from app.models import Message, MessageDelivery, OutboxEntry, Tenant
from app.services.messaging.dispatch import dispatch_message
from tests.conftest import World


async def set_settings(client, world: World, **kw) -> None:  # noqa: ANN001, ANN003
    r = await client.put("/api/v1/admin/settings", json={"settings": kw}, headers=world["leader"].headers)
    assert r.status_code == 200, r.text


@pytest.fixture(autouse=True)
async def relaxed_limits(client, world: World):  # noqa: ANN001, ANN201
    """Default for these tests: no quiet hours, a high weekly cap. Tests tighten as needed."""
    await set_settings(client, world, quiet_hours={"start": "00:00", "end": "00:00"}, weekly_message_cap=100)
    yield


async def draft(client, world: World, students: list[str], mtype: str = "logistics", actor: str = "teacher_a",  # noqa: ANN001
                **variables: str) -> dict:
    variables = {"meet_time": "07:00", "pickup_time": "16:00", "kit_list": "water", **variables}
    r = await client.post("/api/v1/messages/draft", json={
        "message_type": mtype, "title": f"test {mtype}", "student_ids": students,
        "edition_id": str(world.ids["edition"]), "variables": variables},
        headers=world[actor].headers)
    assert r.status_code == 201, r.text
    return r.json()["message"]


async def preview_and_release(client, world: World, mid: str, actor: str = "admin", **body) -> dict:  # noqa: ANN001, ANN003
    p = await client.post(f"/api/v1/messages/{mid}/preview", headers=world[actor].headers)
    assert p.status_code == 200, p.text
    r = await client.post(f"/api/v1/messages/{mid}/release", json=body, headers=world[actor].headers)
    assert r.status_code == 200, r.text
    return (await client.get(f"/api/v1/messages/{mid}/delivery", headers=world[actor].headers)).json()


async def test_teacher_drafts_but_only_a_named_approver_releases(client, world: World) -> None:  # noqa: ANN001
    m = await draft(client, world, [str(world.ids["a1"])])
    r = await client.post(f"/api/v1/messages/{m['id']}/release", json={}, headers=world["teacher_a"].headers)
    assert r.status_code == 403
    early = await client.post(f"/api/v1/messages/{m['id']}/release", json={}, headers=world["admin"].headers)
    assert early.status_code == 409 and "Preview" in early.json()["detail"]
    out = await preview_and_release(client, world, m["id"])
    assert out["released_by_id"] == str(world["admin"].user_id)
    assert out["counts"] == {"sent": 1}
    audit = (await client.get(f"/api/v1/audit?subject_id={m['id']}", headers=world["leader"].headers)).json()
    rel = next(e for e in audit["items"] if e["action"] == "message.release")
    assert rel["actor_user_id"] == str(world["admin"].user_id)
    assert rel["context"]["approver_name"] == "Adam Admin"


async def test_every_send_passes_the_release_gate(world: World) -> None:  # noqa: ANN001
    """Even calling the dispatcher directly cannot send an unreleased message."""
    async with tenant_session(world.tenant) as s:
        tenant = await s.get(Tenant, world.tenant)
        m = Message(message_type="logistics", title="sneaky", student_ids=[world.ids["a1"]])
        s.add(m)
        await s.flush()
        out = await dispatch_message(s, tenant, m, datetime.now(UTC))
        assert out["dispatched"] is False
        await s.rollback()


async def test_preview_shows_three_real_recipients_and_safeguards(client, world: World) -> None:  # noqa: ANN001
    ids = [str(world.ids[k]) for k in ("a1", "a2", "b1", "sib")]
    m = await draft(client, world, ids, actor="admin")
    p = (await client.post(f"/api/v1/messages/{m['id']}/preview", headers=world["admin"].headers)).json()
    assert p["family_count"] == 3  # Ava and Sam share a guardian
    assert len(p["samples"]) == 3
    for sample in p["samples"]:
        assert sample["body"] and "07:00" in sample["body"]
        assert "opt-out" in sample["body"] or "إيقاف" in sample["body"]
        assert sample["weekly_cap"] == 100 and "sent_this_week" in sample
    arabic = next(x for x in p["samples"] if x["language"] == "ar")
    assert "الترتيبات" in arabic["subject"]


async def test_siblings_get_one_message_per_family(client, world: World) -> None:  # noqa: ANN001
    m = await draft(client, world, [str(world.ids["a1"]), str(world.ids["sib"])])
    out = await preview_and_release(client, world, m["id"])
    assert len(out["deliveries"]) == 1
    d = out["deliveries"][0]
    assert set(d["student_ids"]) == {str(world.ids["a1"]), str(world.ids["sib"])}
    assert "Ava" in d["body"] and "Sam" in d["body"]


async def test_negative_news_is_never_sent_by_the_platform(client, world: World) -> None:  # noqa: ANN001
    m = await draft(client, world, [str(world.ids["a1"])], mtype="attendance_concern", squad_name="Squad A")
    await client.post(f"/api/v1/messages/{m['id']}/preview", headers=world["admin"].headers)
    r = await client.post(f"/api/v1/messages/{m['id']}/release", json={}, headers=world["leader"].headers)
    assert r.status_code == 422
    h = await client.post(f"/api/v1/messages/{m['id']}/handoff", headers=world["admin"].headers)
    assert h.json()["message"]["status"] == "manual_handoff"
    async with tenant_session(world.tenant) as s:
        msg = await s.get(Message, uuid.UUID(m["id"]))
        tenant = await s.get(Tenant, world.tenant)
        assert (await dispatch_message(s, tenant, msg, datetime.now(UTC)))["dispatched"] is False
        outbox = (await s.scalars(select(OutboxEntry))).all()
        assert not any(o.meta.get("message_id") == m["id"] for o in outbox)
    d = (await client.get(f"/api/v1/messages/{m['id']}/delivery", headers=world["admin"].headers)).json()["deliveries"][0]
    logged = await client.post(f"/api/v1/messages/{m['id']}/deliveries/{d['delivery_id']}/sent-personally",
                               json={"channel_note": "phoned at 14:10"}, headers=world["admin"].headers)
    assert logged.status_code == 200


async def test_quiet_hours_hold_and_emergency_bypass(client, world: World) -> None:  # noqa: ANN001
    local = datetime.now(UTC).astimezone(ZoneInfo("Asia/Dubai"))
    start, end = (local - timedelta(hours=1)).strftime("%H:%M"), (local + timedelta(hours=1)).strftime("%H:%M")
    await set_settings(client, world, quiet_hours={"start": start, "end": end})
    m = await draft(client, world, [str(world.ids["a1"])])
    out = await preview_and_release(client, world, m["id"])
    assert out["counts"] == {"held_quiet_hours": 1}
    assert out["deliveries"][0]["hold_until"] is not None

    e = await client.post("/api/v1/messages/emergency", json={
        "reason": "Bus breakdown on the way back from the venue", "title": "Late return",
        "notice_en": "The bus has broken down; students are safe and will return at 21:30.",
        "notice_ar": "تعطّلت الحافلة؛ الطلاب بأمان وسيعودون الساعة 21:30.", "squad_ids": [str(world.ids["squad_a"])],
        "confirm": True}, headers=world["leader"].headers)
    assert e.status_code == 200, e.text
    eid = e.json()["message"]["id"]
    delivered = (await client.get(f"/api/v1/messages/{eid}/delivery", headers=world["leader"].headers)).json()
    assert set(delivered["counts"]) == {"sent"}
    audit = (await client.get(f"/api/v1/audit?subject_id={eid}", headers=world["leader"].headers)).json()
    assert any(x["action"] == "message.emergency_broadcast" and "Bus breakdown" in x["reason"] for x in audit["items"])


async def test_weekly_cap_throttles_and_preview_warns(client, world: World) -> None:  # noqa: ANN001
    async with tenant_session(world.tenant) as s:
        already = len((await s.scalars(select(MessageDelivery).where(
            MessageDelivery.guardian_id == world.ids["g_b"], MessageDelivery.status.in_(["sent", "delivered", "read"])))).all())
    await set_settings(client, world, weekly_message_cap=already + 1)
    first = await draft(client, world, [str(world.ids["b1"])], actor="admin")
    p = (await client.post(f"/api/v1/messages/{first['id']}/preview", headers=world["admin"].headers)).json()
    assert p["families_one_below_cap"] == 1
    assert (await preview_and_release(client, world, first["id"]))["counts"] == {"sent": 1}
    second = await draft(client, world, [str(world.ids["b1"])], actor="admin")
    out = await preview_and_release(client, world, second["id"])
    assert out["counts"] == {"throttled": 1}


async def test_consent_withdrawal_blocks_the_next_scheduled_send(client, world: World) -> None:  # noqa: ANN001
    later = datetime.now(UTC) + timedelta(days=2)
    m = await draft(client, world, [str(world.ids["a2"])], actor="admin")
    out = await preview_and_release(client, world, m["id"], scheduled_for=later.isoformat())
    assert out["counts"] == {"pending": 1}  # scheduled, not yet sent

    from tests.conftest import make_user
    from app.models.enums import Role, ScopeType

    parent = await make_user(world.tenant, f"ali.parent+{uuid.uuid4().hex[:4]}@example.com", "Parent of Ali",
                             [(Role.PARENT, ScopeType.GUARDIAN, world.ids["g_a2"])], mfa=False)
    w = await client.post("/api/v1/portal/consents/withdraw", json={"student_id": str(world.ids["a2"]),
                                                                   "purpose": "communications"}, headers=parent.headers)
    assert w.status_code == 200

    async with tenant_session(world.tenant) as s:
        tenant = await s.get(Tenant, world.tenant)
        msg = await s.get(Message, uuid.UUID(m["id"]))
        await dispatch_message(s, tenant, msg, later + timedelta(minutes=1))
        await s.commit()
    after = (await client.get(f"/api/v1/messages/{m['id']}/delivery", headers=world["admin"].headers)).json()
    assert after["counts"] == {"blocked_consent": 1}


async def test_one_tap_opt_out_is_honoured_immediately(client, world: World) -> None:  # noqa: ANN001
    m = await draft(client, world, [str(world.ids["b1"])], mtype="celebration", actor="admin",
                    celebration_note="Great work", celebration_note_ar="عمل رائع")
    out = await preview_and_release(client, world, m["id"])
    body = out["deliveries"][0]["body"]
    token = body.split("token=")[1].split()[0]
    r = await client.post("/api/v1/portal/opt-out-link", json={"token": token})
    assert r.status_code == 200 and r.json()["category"] == "celebration"
    d = await client.post("/api/v1/messages/draft", json={
        "message_type": "celebration", "title": "again", "student_ids": [str(world.ids["b1"])],
        "variables": {"celebration_note": "x", "celebration_note_ar": "x"}}, headers=world["admin"].headers)
    assert d.json()["families_opted_out_of_category"] == 1  # visible before drafting further
    out2 = await preview_and_release(client, world, d.json()["message"]["id"])
    assert out2["counts"] == {"blocked_opt_out": 1}


async def test_whatsapp_needs_an_approved_template_else_email(client, world: World) -> None:  # noqa: ANN001
    async with tenant_session(world.tenant) as s:
        from app.models import Guardian

        g = await s.get(Guardian, world.ids["g_b"])
        g.preferred_channel = "whatsapp"
        await s.commit()
    m = await draft(client, world, [str(world.ids["b1"])], actor="admin")  # logistics: WA template approved
    out = await preview_and_release(client, world, m["id"])
    assert out["deliveries"][0]["channel_used"] == "whatsapp"
    c = await draft(client, world, [str(world.ids["b1"])], mtype="consent_request", actor="admin",
                    consent_label="travel consent", consent_label_ar="موافقة السفر", consent_purpose="travel")
    out = await preview_and_release(client, world, c["id"])
    assert out["deliveries"][0]["channel_used"] == "email"  # no WA template for consent requests
    async with tenant_session(world.tenant) as s:
        from app.models import Guardian

        g = await s.get(Guardian, world.ids["g_b"])
        g.preferred_channel = "email"
        await s.commit()


async def test_unapproved_template_version_cannot_be_released(client, world: World) -> None:  # noqa: ANN001
    t = await client.post("/api/v1/message-templates", json={
        "key": "event_logistics", "message_type": "logistics", "subject_en": "New {{ edition_name }}",
        "body_en": "Hi {{ guardian_name }}", "subject_ar": "جديد", "body_ar": "مرحبا {{ guardian_name }}"},
        headers=world["admin"].headers)
    assert t.status_code == 201 and t.json()["status"] == "draft" and t.json()["version"] == 2
    r = await client.post("/api/v1/messages/draft", json={"message_type": "logistics", "title": "v2",
                                                         "template_id": t.json()["id"], "student_ids": [str(world.ids["a1"])]},
                          headers=world["admin"].headers)
    mid = r.json()["message"]["id"]
    await client.post(f"/api/v1/messages/{mid}/preview", headers=world["admin"].headers)
    rel = await client.post(f"/api/v1/messages/{mid}/release", json={}, headers=world["admin"].headers)
    assert rel.status_code == 409
    bad = await client.post("/api/v1/message-templates", json={
        "key": "broken", "message_type": "logistics", "subject_en": "{{ unclosed", "body_en": "x",
        "subject_ar": "x", "body_ar": "x"}, headers=world["admin"].headers)
    assert bad.status_code == 422


async def test_consent_request_is_tracked_to_a_response(client, world: World) -> None:  # noqa: ANN001
    c = await draft(client, world, [str(world.ids["a1"])], mtype="consent_request", actor="admin",
                    consent_label="travel consent", consent_label_ar="موافقة السفر", consent_purpose="travel")
    await preview_and_release(client, world, c["id"])
    overview = (await client.get(f"/api/v1/portal/children/{world.ids['a1']}", headers=world["parent_a"].headers)).json()
    req = next(r for r in overview["pending_consent_requests"] if r["purpose"] == "travel")
    r = await client.post("/api/v1/portal/consents", json={
        "student_id": str(world.ids["a1"]), "purpose": "travel", "decision": "granted",
        "edition_id": str(world.ids["edition"]), "consent_request_id": req["id"]}, headers=world["parent_a"].headers)
    assert r.status_code == 201 and r.json()["version"] == 1
    overview = (await client.get(f"/api/v1/portal/children/{world.ids['a1']}", headers=world["parent_a"].headers)).json()
    assert not [x for x in overview["pending_consent_requests"] if x["id"] == req["id"]]
