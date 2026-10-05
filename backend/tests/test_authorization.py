"""Authorization suite (acceptance criterion 13.1.2): no student record is reachable by a
user outside their permitted scope. Each case *attempts* the access and asserts refusal.

Out-of-scope records answer 404 (indistinguishable from "does not exist"); a missing
capability answers 403.
"""

import pytest

from tests.conftest import World, ymd

DENY = (403, 404)


def student_reads(sid: str, edition: str) -> list[tuple[str, str]]:
    return [
        ("GET", f"/api/v1/students/{sid}"),
        ("GET", f"/api/v1/students/{sid}/profile"),
        ("GET", f"/api/v1/students/{sid}/readiness?edition_id={edition}"),
        ("GET", f"/api/v1/students/{sid}/recommendations"),
        ("GET", f"/api/v1/students/{sid}/goals"),
        ("GET", f"/api/v1/students/{sid}/consents"),
        ("GET", f"/api/v1/students/{sid}/export?reason=testing"),
    ]


CASES = [
    # (actor, target student key, description)
    ("teacher_a", "b1", "teacher cannot read a student in another teacher's squad"),
    ("teacher_b", "a1", "teacher cannot read a student in another teacher's squad"),
    ("parent_a", "b1", "parent cannot use staff endpoints, even for another family"),
    ("parent_a", "a1", "parent cannot use staff endpoints for their own child"),
    ("student_a", "a1", "student cannot read another student"),
    ("beta_leader", "a1", "a leader in another school cannot reach this school's students"),
]


@pytest.mark.parametrize(("actor", "target", "why"), CASES)
async def test_student_record_unreachable_outside_scope(client, world: World, actor: str, target: str, why: str) -> None:  # noqa: ANN001
    for method, url in student_reads(str(world.ids[target]), str(world.ids["edition"])):
        r = await client.request(method, url, headers=world[actor].headers)
        assert r.status_code in DENY, f"{why}: {method} {url} -> {r.status_code}"


async def test_teacher_sees_own_squad_students_only(client, world: World) -> None:  # noqa: ANN001
    r = await client.get("/api/v1/students", headers=world["teacher_a"].headers)
    assert r.status_code == 200
    ids = {s["id"] for s in r.json()["items"]}
    assert str(world.ids["a1"]) in ids and str(world.ids["b1"]) not in ids
    ok = await client.get(f"/api/v1/students/{world.ids['a1']}", headers=world["teacher_a"].headers)
    assert ok.status_code == 200


async def test_whole_school_roles_see_everyone(client, world: World) -> None:  # noqa: ANN001
    for actor in ("leader", "admin"):
        r = await client.get(f"/api/v1/students/{world.ids['b1']}", headers=world[actor].headers)
        assert r.status_code == 200


async def test_teacher_cannot_write_to_another_squad(client, world: World) -> None:  # noqa: ANN001
    h = world["teacher_a"].headers
    b1, sq_b = str(world.ids["b1"]), str(world.ids["squad_b"])
    attempts = [
        ("POST", "/api/v1/skills/awards", {"student_id": b1, "skill_id": str(world.ids["skill_loop"]), "level": 2}),
        ("POST", "/api/v1/results", {"edition_id": str(world.ids["edition"]), "squad_id": sq_b,
                                     "participant_ids": [b1], "placement": 1, "field_size": 10}),
        ("POST", "/api/v1/sessions", {"squad_id": sq_b, "starts_at": f"{ymd()}T15:00:00Z"}),
        ("POST", f"/api/v1/squads/{sq_b}/members", {"student_id": str(world.ids["a1"])}),
        ("POST", f"/api/v1/students/{b1}/goals", {"skill_id": str(world.ids["skill_loop"]), "target_level": 3}),
        ("POST", "/api/v1/skills/awards/quick-tag", {"skill_id": str(world.ids["skill_loop"]), "level": 2,
                                                     "student_ids": [b1]}),
        ("POST", "/api/v1/messages/draft", {"message_type": "logistics", "title": "x", "student_ids": [b1]}),
    ]
    for method, url, body in attempts:
        r = await client.request(method, url, json=body, headers=h)
        assert r.status_code in DENY, f"{method} {url} -> {r.status_code} {r.text}"


async def test_teacher_cannot_reach_other_squad_by_id(client, world: World) -> None:  # noqa: ANN001
    h = world["teacher_a"].headers
    sq_b = world.ids["squad_b"]
    for url in (f"/api/v1/squads/{sq_b}", f"/api/v1/squads/{sq_b}/readiness", f"/api/v1/sessions?squad_id={sq_b}",
                f"/api/v1/results?squad_id={sq_b}"):
        r = await client.get(url, headers=h)
        assert r.status_code in DENY, f"{url} -> {r.status_code}"
    squads = (await client.get("/api/v1/squads", headers=h)).json()
    assert {s["id"] for s in squads} == {str(world.ids["squad_a"])}


async def test_teacher_attendance_on_other_squads_session_refused(client, world: World) -> None:  # noqa: ANN001
    s = await client.post("/api/v1/sessions", json={"squad_id": str(world.ids["squad_b"]),
                                                    "starts_at": f"{ymd()}T15:00:00Z"}, headers=world["teacher_b"].headers)
    assert s.status_code == 201
    r = await client.post(f"/api/v1/sessions/{s.json()['id']}/attendance",
                          json={"records": [{"student_id": str(world.ids["b1"]), "status": "present"}]},
                          headers=world["teacher_a"].headers)
    assert r.status_code in DENY


EMERGENCY = {"reason": "test of the permission check", "title": "x", "notice_en": "hello", "notice_ar": "مرحبا",
             "whole_school": True}


def _result_body(world: World) -> dict:
    return {"edition_id": str(world.ids["edition"]), "squad_id": str(world.ids["squad_a"]),
            "participant_ids": [str(world.ids["a1"])], "placement": 1, "field_size": 5}


@pytest.mark.parametrize(("actor", "method", "url", "body"), [
    ("teacher_a", "GET", "/api/v1/dashboard/leader", None),
    ("teacher_a", "GET", "/api/v1/budget", None),
    ("teacher_a", "GET", "/api/v1/admin/users", None),
    ("teacher_a", "GET", "/api/v1/audit", None),
    ("teacher_a", "POST", "/api/v1/reports/inspection_evidence/generate", {}),
    ("teacher_a", "POST", "/api/v1/imports/mis/commit", {"batch_id": "00000000-0000-0000-0000-000000000000"}),
    ("teacher_a", "POST", "/api/v1/messages/emergency", EMERGENCY),
    ("leader", "POST", "/api/v1/results", "result"),
    ("leader", "POST", "/api/v1/skills/awards/bulk-confirm", {}),
    ("admin", "GET", "/api/v1/admin/users", None),
    ("admin", "POST", "/api/v1/messages/emergency", EMERGENCY),
    ("admin_no_mfa", "GET", "/api/v1/budget", None),
    ("admin_no_mfa", "GET", "/api/v1/students", None),
    ("parent_a", "GET", "/api/v1/students", None),
    ("parent_a", "GET", "/api/v1/squads", None),
    ("student_a", "GET", "/api/v1/students", None),
    ("student_a", "GET", "/api/v1/portal/messages", None),
])
async def test_capability_matrix(client, world: World, actor: str, method: str, url: str, body) -> None:  # noqa: ANN001
    if body == "result":
        body = _result_body(world)
    r = await client.request(method, url, json=body, headers=world[actor].headers)
    assert r.status_code == 403, f"{actor} {method} {url} -> {r.status_code} {r.text[:200]}"


async def test_parent_portal_is_scoped_to_own_children(client, world: World) -> None:  # noqa: ANN001
    h = world["parent_a"].headers
    assert (await client.get(f"/api/v1/portal/children/{world.ids['a1']}", headers=h)).status_code == 200
    assert (await client.get(f"/api/v1/portal/children/{world.ids['sib']}", headers=h)).status_code == 200
    for other in ("b1", "a2", "beta_student"):
        r = await client.get(f"/api/v1/portal/children/{world.ids[other]}", headers=h)
        assert r.status_code == 404, other
    r = await client.post("/api/v1/portal/consents/withdraw", json={"student_id": str(world.ids["b1"]),
                                                                   "purpose": "media"}, headers=h)
    assert r.status_code == 404


async def test_unauthenticated_and_forged_tokens_rejected(client, world: World) -> None:  # noqa: ANN001
    assert (await client.get("/api/v1/students")).status_code == 401
    forged = world["leader"].token[:-4] + "abcd"
    assert (await client.get("/api/v1/students", headers={"Authorization": f"Bearer {forged}"})).status_code == 401


async def test_reads_of_student_records_are_audited(client, world: World) -> None:  # noqa: ANN001
    sid = world.ids["a2"]
    await client.get(f"/api/v1/students/{sid}/profile", headers=world["teacher_a"].headers)
    r = await client.get(f"/api/v1/audit?subject_id={sid}", headers=world["leader"].headers)
    actions = {e["action"] for e in r.json()["items"]}
    assert "student.profile_read" in actions
