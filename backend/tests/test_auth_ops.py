"""Authentication flows, operations modules, webhooks, insights, retention."""

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

import pyotp
import respx
from httpx import Response
from sqlalchemy import select

from app.db import tenant_session
from app.models import Insight, OutboxEntry, Student, Tenant, User, WebhookDelivery
from app.models.enums import EnrolmentStatus, Role, ScopeType
from app.security import encrypt_field
from tests.conftest import World, make_user


async def _slug(world: World) -> str:
    async with tenant_session(world.tenant) as s:
        return (await s.get(Tenant, world.tenant)).slug


# ---------------- Auth ----------------
async def test_leader_login_requires_totp_and_enrolment(client, world: World) -> None:  # noqa: ANN001
    slug = await _slug(world)
    await make_user(world.tenant, "newleader@alpha.test", "New Leader", [(Role.LEADER, ScopeType.SCHOOL, None)],
                    password="correct horse battery staple")
    bad = await client.post("/api/v1/auth/password-login", json={"tenant": slug, "email": "newleader@alpha.test",
                                                                  "password": "wrong"})
    assert bad.status_code == 401
    r = await client.post("/api/v1/auth/password-login", json={"tenant": slug, "email": "newleader@alpha.test",
                                                                "password": "correct horse battery staple"})
    assert r.json()["status"] == "mfa_enrolment_required" and r.json()["access_token"] is None
    enrol = await client.post("/api/v1/auth/mfa/enrol", json={"token": r.json()["login_token"]})
    secret = enrol.json()["secret"]
    assert enrol.json()["otpauth_uri"].startswith("otpauth://totp/")
    wrong = await client.post("/api/v1/auth/mfa/verify", json={"login_token": r.json()["login_token"], "code": "000000"})
    assert wrong.status_code == 401
    ok = await client.post("/api/v1/auth/mfa/verify", json={"login_token": r.json()["login_token"],
                                                            "code": pyotp.TOTP(secret).now()})
    assert ok.status_code == 200 and ok.json()["access_token"]
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {ok.json()['access_token']}"})
    assert me.json()["mfa"] is True and "approve_spend" in me.json()["capabilities"]
    # Next time: password then code.
    again = await client.post("/api/v1/auth/password-login", json={"tenant": slug, "email": "newleader@alpha.test",
                                                                    "password": "correct horse battery staple"})
    assert again.json()["status"] == "mfa_required"


async def test_teacher_login_needs_no_second_factor(client, world: World) -> None:  # noqa: ANN001
    slug = await _slug(world)
    await make_user(world.tenant, "plainteacher@alpha.test", "Plain Teacher", [(Role.TEACHER, ScopeType.SCHOOL, None)],
                    password="another long password")
    r = await client.post("/api/v1/auth/password-login", json={"tenant": slug, "email": "plainteacher@alpha.test",
                                                                "password": "another long password"})
    assert r.json()["status"] == "ok" and r.json()["access_token"]
    assert "stem_refresh" in r.cookies


async def test_refresh_rotation_and_reuse_detection(client, world: World) -> None:  # noqa: ANN001
    slug = await _slug(world)
    await make_user(world.tenant, "rotate@alpha.test", "Rotating Teacher", [(Role.TEACHER, ScopeType.SCHOOL, None)],
                    password="rotate rotate rotate")
    login = await client.post("/api/v1/auth/password-login", json={"tenant": slug, "email": "rotate@alpha.test",
                                                                    "password": "rotate rotate rotate"})
    first = login.cookies["stem_refresh"]
    no_header = await client.post("/api/v1/auth/refresh", cookies={"stem_refresh": first})
    assert no_header.status_code == 403  # CSRF guard
    hdr = {"X-Requested-With": "stemtrack"}
    r1 = await client.post("/api/v1/auth/refresh", cookies={"stem_refresh": first}, headers=hdr)
    assert r1.status_code == 200 and r1.json()["access_token"]
    second = r1.cookies["stem_refresh"]
    assert second != first
    replay = await client.post("/api/v1/auth/refresh", cookies={"stem_refresh": first}, headers=hdr)
    assert replay.status_code == 401  # reuse of a rotated token...
    after = await client.post("/api/v1/auth/refresh", cookies={"stem_refresh": second}, headers=hdr)
    assert after.status_code == 401  # ...revokes the whole family


async def test_parent_magic_link(client, world: World) -> None:  # noqa: ANN001
    slug = await _slug(world)
    unknown = await client.post("/api/v1/auth/parent/request-link", json={"tenant": slug, "email": "nobody@example.com"})
    assert unknown.status_code == 202  # no account enumeration
    r = await client.post("/api/v1/auth/parent/request-link", json={"tenant": slug, "email": "bea.parent@example.com"})
    assert r.status_code == 202
    async with tenant_session(world.tenant) as s:
        mail = (await s.scalars(select(OutboxEntry).where(OutboxEntry.recipient == "bea.parent@example.com")
                                .order_by(OutboxEntry.created_at.desc()))).first()
    assert mail is not None and "رابط" in mail.subject  # guardian's language is Arabic
    token = mail.body.split("token=")[1].split()[0]
    v = await client.post("/api/v1/auth/parent/verify", json={"token": token})
    assert v.status_code == 200
    me = (await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {v.json()['access_token']}"})).json()
    assert [c["name"] for c in me["children"]] == ["Bea Test"]
    reused = await client.post("/api/v1/auth/parent/verify", json={"token": token})
    assert reused.status_code == 401  # single use


async def test_health_reports_each_dependency(client) -> None:  # noqa: ANN001
    r = await client.get("/api/v1/health")
    assert set(r.json()["checks"]) == {"database", "redis", "object_storage"}
    assert r.json()["checks"]["database"] == "ok"


# ---------------- Operations ----------------
async def test_kit_loan_manifest_and_damage(client, world: World) -> None:  # noqa: ANN001
    admin = world["admin"].headers
    a = (await client.post("/api/v1/inventory", json={"tag": "KIT-T1", "name": "Test kit", "category": "Robotics kits",
                                                      "quantity_on_hand": 2}, headers=admin)).json()
    loan = (await client.post("/api/v1/inventory/loans", json={"asset_id": a["id"], "edition_id": str(world.ids["edition"]),
                                                               "quantity": 2}, headers=admin)).json()
    over = await client.post("/api/v1/inventory/loans", json={"asset_id": a["id"], "quantity": 1}, headers=admin)
    assert over.status_code == 409
    man = (await client.get(f"/api/v1/inventory/editions/{world.ids['edition']}/manifest", headers=admin)).json()
    assert man["out"] >= 1
    back = await client.post(f"/api/v1/inventory/loans/{loan['id']}/return",
                             json={"returned_quantity": 1, "return_condition": "needs_repair"}, headers=admin)
    assert back.json()["returned_quantity"] == 1
    inv = {x["tag"]: x for x in (await client.get("/api/v1/inventory", headers=admin)).json()}
    assert inv["KIT-T1"]["quantity_on_hand"] == 1 and inv["KIT-T1"]["condition"] == "needs_repair"
    # Teachers request kit; they cannot issue it.
    assert (await client.post("/api/v1/inventory/requests", json={"description": "2 more sensors"},
                              headers=world["teacher_a"].headers)).status_code == 201
    assert (await client.post("/api/v1/inventory/loans", json={"asset_id": a["id"]},
                              headers=world["teacher_a"].headers)).status_code == 403


async def test_budget_admin_plans_leader_approves(client, world: World) -> None:  # noqa: ANN001
    line = await client.post("/api/v1/budget/lines", json={"edition_id": str(world.ids["edition"]), "category": "transport",
                                                          "description": "Bus", "planned_amount": "800"},
                             headers=world["admin"].headers)
    assert line.status_code == 201 and line.json()["status"] == "proposed"
    assert (await client.post(f"/api/v1/budget/lines/{line.json()['id']}/decide", json={"decision": "approved"},
                              headers=world["admin"].headers)).status_code == 403
    ok = await client.post(f"/api/v1/budget/lines/{line.json()['id']}/decide", json={"decision": "approved",
                                                                                      "note": "Within envelope"},
                           headers=world["leader"].headers)
    assert ok.json()["status"] == "approved" and ok.json()["approved_by_id"] == str(world["leader"].user_id)
    overview = (await client.get("/api/v1/budget", headers=world["leader"].headers)).json()
    assert overview["by_edition"]


async def test_outbound_webhooks_are_signed(client, world: World) -> None:  # noqa: ANN001
    sub = await client.post("/api/v1/admin/webhooks", json={"url": "https://hooks.example.org/stem",
                                                           "events": ["result.recorded"]}, headers=world["leader"].headers)
    assert sub.status_code == 201
    secret = sub.json()["secret"]
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://hooks.example.org/stem").mock(return_value=Response(204))
        r = await client.post("/api/v1/results", json={
            "edition_id": str(world.ids["edition"]), "squad_id": str(world.ids["squad_a"]),
            "participant_ids": [str(world.ids["a1"])], "placement": 5, "field_size": 30}, headers=world["teacher_a"].headers)
        assert r.status_code == 201
        req = route.calls.last.request
        expected = hmac.new(secret.encode(), req.content, hashlib.sha256).hexdigest()
        assert req.headers["X-Stemtrack-Signature"] == f"sha256={expected}"
        payload = json.loads(req.content)
        assert payload["event"] == "result.recorded" and "name" not in json.dumps(payload).lower()
    async with tenant_session(world.tenant) as s:
        d = (await s.scalars(select(WebhookDelivery).order_by(WebhookDelivery.created_at.desc()))).first()
        assert d.status == "delivered"
    await client.delete(f"/api/v1/admin/webhooks/{sub.json()['id']}", headers=world["leader"].headers)


async def test_insights_are_drafts_until_a_teacher_approves(client, world: World) -> None:  # noqa: ANN001
    h = world["teacher_a"].headers
    g = await client.post("/api/v1/insights/generate", json={"squad_id": str(world.ids["squad_a"])}, headers=h)
    assert g.status_code == 200
    items = (await client.get("/api/v1/insights", headers=h)).json()
    assert items and all(i["status"] == "draft" for i in items)
    i = items[0]
    assert i["facts"]["period"] and i["claim_type"] in ("measured", "inferred")
    r = await client.post(f"/api/v1/insights/{i['id']}/review", json={"decision": "approved",
                                                                      "edited_text_en": i["text_en"] + " (checked)"},
                          headers=h)
    assert r.status_code == 200
    async with tenant_session(world.tenant) as s:
        ins = await s.get(Insight, uuid.UUID(i["id"]))
        assert ins.status.value == "approved" and ins.reviewed_by_id == world["teacher_a"].user_id
        assert ins.text_en != ins.edited_text_en  # the generated text is kept for audit


async def test_retention_anonymises_long_gone_leavers(world: World) -> None:  # noqa: ANN001
    from app.services.retention import purge
    from app.tenancy import TenantSettings

    async with tenant_session(world.tenant) as s:
        old = Student(external_mis_id="OLD-1", given_name="Gone", family_name="Long", year_group=13,
                      enrolment_status=EnrolmentStatus.LEFT, left_on=(datetime.now(UTC) - timedelta(days=4 * 365)).date())
        s.add(old)
        await s.flush()
        out = await purge(s, TenantSettings())
        await s.commit()
        assert out["students_anonymised"] >= 1
        again = await s.get(Student, old.id)
        assert again.given_name == "Former" and again.anonymised_at is not None and again.date_of_birth is None


async def test_squad_calendar_feed_contains_no_student_data(client, world: World) -> None:  # noqa: ANN001
    link = (await client.post(f"/api/v1/squads/{world.ids['squad_a']}/calendar-link",
                              headers=world["teacher_a"].headers)).json()["url"]
    ics = await client.get("/api/v1/" + link.split("/api/v1/", 1)[1])
    assert ics.status_code == 200 and ics.text.startswith("BEGIN:VCALENDAR")
    assert "Robo Cup Qualifier" in ics.text and "Ava" not in ics.text


async def test_settings_tier_weights_and_audit(client, world: World) -> None:  # noqa: ANN001
    r = await client.put("/api/v1/admin/settings", json={"settings": {"readiness_threshold": "0.7"}},
                         headers=world["admin"].headers)
    assert r.json()["settings"]["readiness_threshold"] == "0.7"
    bad = await client.put("/api/v1/admin/settings", json={"settings": {"readiness_threshold": "1.5"}},
                           headers=world["admin"].headers)
    assert bad.status_code == 422
    sso = await client.put("/api/v1/admin/settings", json={"settings": {"oidc": {"issuer": "https://login.example"}}},
                           headers=world["admin"].headers)
    assert sso.status_code == 403  # only leaders change SSO
    await client.put("/api/v1/admin/settings", json={"settings": {"readiness_threshold": "0.65"}},
                     headers=world["admin"].headers)


async def test_user_management_is_leader_only(client, world: World) -> None:  # noqa: ANN001
    body = {"email": "newstaff@alpha.test", "display_name": "New Staff", "roles": [{"role": "teacher"}]}
    assert (await client.post("/api/v1/admin/users", json=body, headers=world["admin"].headers)).status_code == 403
    r = await client.post("/api/v1/admin/users", json=body, headers=world["leader"].headers)
    assert r.status_code == 201 and r.json()["roles"][0]["role"] == "teacher"
    bad = await client.post(f"/api/v1/admin/users/{r.json()['id']}/roles", json={"role": "parent", "scope_type": "school"},
                            headers=world["leader"].headers)
    assert bad.status_code == 422  # a parent role must be scoped to a guardian


async def test_full_data_export_excludes_credentials(client, world: World) -> None:  # noqa: ANN001
    import io
    import zipfile

    async with tenant_session(world.tenant) as s:
        u = await s.get(User, world["leader"].user_id)
        u.mfa_secret_enc = encrypt_field("SECRET")
        await s.commit()
    r = await client.get("/api/v1/admin/export?reason=annual%20archive", headers=world["leader"].headers)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    users = z.read("users.json").decode()
    assert "password_hash" not in users and "mfa_secret_enc" not in users
    assert "students.json" in z.namelist() and "refresh_tokens.json" not in z.namelist()
