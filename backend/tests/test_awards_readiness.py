"""Entering is not demonstrating: proposed awards don't count until a named teacher confirms.
Readiness is traceable to the exact awards and requirements (acceptance criterion 13.1.3)."""

from app.models.enums import Role, ScopeType
from tests.conftest import World, make_user


async def _readiness(client, world: World, sid: str) -> dict:  # noqa: ANN001
    r = await client.get(
        f"/api/v1/students/{sid}/readiness?edition_id={world.ids['edition']}",
        headers=world["teacher_a"].headers,
    )
    assert r.status_code == 200, r.text
    return r.json()


async def test_rubric_result_proposes_but_does_not_grant(client, world: World) -> None:  # noqa: ANN001
    sib = str(world.ids["sib"])
    before = await _readiness(client, world, sib)
    loop_before = next(x for x in before["lines"] if x["code"] == "PROG.LOOP.02")
    assert loop_before["earned_level"] == 0
    res = await client.post(
        "/api/v1/results",
        json={
            "edition_id": str(world.ids["edition"]),
            "squad_id": str(world.ids["squad_a"]),
            "participant_ids": [sib],
            "placement": 2,
            "field_size": 20,
            "rubric_scores": {"programming": 4},
        },
        headers=world["teacher_a"].headers,
    )
    assert res.json()["proposed_award_count"] == 1
    after = await _readiness(client, world, sib)
    assert next(x for x in after["lines"] if x["code"] == "PROG.LOOP.02")["earned_level"] == 0, (
        "proposed must not count"
    )

    proposed = (
        await client.get(
            f"/api/v1/skills/awards/proposed?result_id={res.json()['id']}", headers=world["teacher_a"].headers
        )
    ).json()
    assert len(proposed) == 1 and proposed[0]["status"] == "proposed" and proposed[0]["source"] == "rubric"
    ok = await client.post(
        "/api/v1/skills/awards/bulk-confirm",
        json={"confirm": [proposed[0]["id"]]},
        headers=world["teacher_a"].headers,
    )
    assert ok.status_code == 200
    confirmed = await _readiness(client, world, sib)
    line = next(x for x in confirmed["lines"] if x["code"] == "PROG.LOOP.02")
    assert line["earned_level"] == 3
    assert line["evidence"]["award_id"] == proposed[0]["id"]
    assert line["evidence"]["verified_by_id"] == str(world["teacher_a"].user_id)
    assert confirmed["score"] > before["score"]


async def test_readiness_trace_names_requirements_and_formula(client, world: World) -> None:  # noqa: ANN001
    out = await _readiness(client, world, str(world.ids["a1"]))
    assert out["formula"].startswith("Σ w·min")
    assert {x["code"] for x in out["lines"]} == {"PROG.LOOP.02", "ROBO.SENS.02", "COL.TEAM.01"}
    assert all(x["requirement_id"] for x in out["lines"])
    assert out["claim_type"] == "inferred"


async def test_teacher_grant_is_verified_immediately_and_idempotent(client, world: World) -> None:  # noqa: ANN001
    body = {
        "idempotency_key": "grant-1",
        "student_id": str(world.ids["a1"]),
        "skill_id": str(world.ids["skill_team"]),
        "level": 2,
        "evidence_note": "Ran the pit crew",
    }
    r1 = await client.post("/api/v1/skills/awards", json=body, headers=world["teacher_a"].headers)
    r2 = await client.post("/api/v1/skills/awards", json=body, headers=world["teacher_a"].headers)
    assert r1.status_code == 201 and r2.status_code == 200 and r1.json()["id"] == r2.json()["id"]
    assert r1.json()["status"] == "verified" and r1.json()["confidence"] == "high"


async def test_rubric_and_self_sources_cannot_be_granted_directly(client, world: World) -> None:  # noqa: ANN001
    for source in ("rubric", "self"):
        r = await client.post(
            "/api/v1/skills/awards",
            json={
                "student_id": str(world.ids["a1"]),
                "skill_id": str(world.ids["skill_loop"]),
                "level": 4,
                "source": source,
            },
            headers=world["teacher_a"].headers,
        )
        assert r.status_code == 422


async def test_self_assessment_needs_countersign(client, world: World) -> None:  # noqa: ANN001
    st = world["student_a"]
    r = await client.post(
        "/api/v1/skills/awards/self-assess",
        json={
            "student_id": str(world.ids["a2"]),
            "skill_id": str(world.ids["skill_sens"]),
            "level": 3,
            "evidence_note": "I calibrated the colour sensor",
        },
        headers=st.headers,
    )
    assert r.status_code == 201 and r.json()["status"] == "proposed" and r.json()["confidence"] == "low"
    other = await client.post(
        "/api/v1/skills/awards/self-assess",
        json={"student_id": str(world.ids["a1"]), "skill_id": str(world.ids["skill_sens"]), "level": 3},
        headers=st.headers,
    )
    assert other.status_code == 404
    before = await _readiness(client, world, str(world.ids["a2"]))
    assert next(x for x in before["lines"] if x["code"] == "ROBO.SENS.02")["earned_level"] == 0
    await client.post(
        "/api/v1/skills/awards/bulk-confirm",
        json={"confirm": [r.json()["id"]]},
        headers=world["teacher_a"].headers,
    )
    after = await _readiness(client, world, str(world.ids["a2"]))
    assert next(x for x in after["lines"] if x["code"] == "ROBO.SENS.02")["earned_level"] == 3


async def test_revocation_requires_reason_and_removes_from_readiness(client, world: World) -> None:  # noqa: ANN001
    a = (
        await client.post(
            "/api/v1/skills/awards",
            json={"student_id": str(world.ids["a2"]), "skill_id": str(world.ids["skill_loop"]), "level": 4},
            headers=world["teacher_a"].headers,
        )
    ).json()
    assert (
        await client.request(
            "DELETE", f"/api/v1/skills/awards/{a['id']}", json={}, headers=world["teacher_a"].headers
        )
    ).status_code == 422
    r = await client.request(
        "DELETE",
        f"/api/v1/skills/awards/{a['id']}",
        json={"reason": "Recorded against wrong student"},
        headers=world["teacher_a"].headers,
    )
    assert r.json()["status"] == "revoked"
    after = await _readiness(client, world, str(world.ids["a2"]))
    assert next(x for x in after["lines"] if x["code"] == "PROG.LOOP.02")["earned_level"] != 4


async def test_edition_overrides_inherited_requirements(client, world: World) -> None:  # noqa: ANN001
    admin = world["admin"].headers
    comp = str(world.ids["competition"])
    ed2 = (
        await client.post(
            f"/api/v1/competitions/{comp}/editions",
            json={
                "name": "Robo Cup Final",
                "event_starts": "2027-03-01",
                "event_ends": "2027-03-02",
                "tier": "national",
            },
            headers=admin,
        )
    ).json()
    inherited = (await client.get(f"/api/v1/editions/{ed2['id']}/requirements", headers=admin)).json()
    assert {r["code"] for r in inherited} == {"PROG.LOOP.02", "ROBO.SENS.02", "COL.TEAM.01"}
    assert all(r["inherited"] for r in inherited)
    out = (
        await client.post(
            f"/api/v1/editions/{ed2['id']}/requirements",
            json=[
                {"skill_code": "PROG.LOOP.02", "required_level": 4, "weight": 3},
                {"skill_code": "COL.TEAM.01", "required_level": 2, "removed": True},
                {"skill_code": "ROBO.CTRL.02", "required_level": 3},
            ],
            headers=admin,
        )
    ).json()
    by = {r["code"]: r for r in out}
    assert set(by) == {"PROG.LOOP.02", "ROBO.SENS.02", "ROBO.CTRL.02"}
    assert by["PROG.LOOP.02"]["required_level"] == 4 and not by["PROG.LOOP.02"]["inherited"]
    assert by["ROBO.SENS.02"]["inherited"]
    # The original edition is unaffected.
    orig = (await client.get(f"/api/v1/editions/{world.ids['edition']}/requirements", headers=admin)).json()
    assert {r["code"]: r["required_level"] for r in orig}["PROG.LOOP.02"] == 3


async def test_squad_readiness_has_shared_gaps_and_reasoned_focus(client, world: World) -> None:  # noqa: ANN001
    r = await client.get(
        f"/api/v1/squads/{world.ids['squad_a']}/readiness", headers=world["teacher_a"].headers
    )
    assert r.status_code == 200
    data = r.json()
    ed = data["editions"][0]
    assert ed["member_count"] == 3 and ed["shared_gaps"]
    for f in data["suggested_session_focus"]:
        assert f["reason_en"] and f["claim_type"] == "inferred"


async def test_recommendations_explain_every_check(client, world: World) -> None:  # noqa: ANN001
    r = await client.get(
        f"/api/v1/students/{world.ids['a1']}/recommendations", headers=world["teacher_a"].headers
    )
    assert r.status_code == 200
    every = r.json()["recommended"] + r.json()["almost_ready"] + r.json()["not_recommended"]
    assert every, "the edition the student is eligible for must appear in one list"
    for item in every:
        assert item["reason_en"] and item["reason_ar"]
        assert {c["key"] for c in item["checks"]} >= {
            "eligible",
            "readiness",
            "registration_open",
            "budget",
            "no_exam_clash",
        }


async def test_exam_window_clash_detected_and_blocks_recommendation(client, world: World) -> None:  # noqa: ANN001
    admin = world["admin"].headers
    ed = (await client.get(f"/api/v1/editions/{world.ids['edition']}", headers=admin)).json()
    w = await client.post(
        "/api/v1/exam-windows",
        json={
            "name": "Mocks",
            "starts_on": ed["event_starts"],
            "ends_on": ed["event_ends"],
            "year_groups": [8],
        },
        headers=admin,
    )
    assert w.status_code == 201
    clashes = (await client.get(f"/api/v1/editions/{world.ids['edition']}/clashes", headers=admin)).json()
    assert clashes[0]["kind"] == "exam_window" and clashes[0]["year_groups"] == [8]
    recs = (await client.get(f"/api/v1/students/{world.ids['a1']}/recommendations", headers=admin)).json()
    item = next(
        x
        for x in recs["recommended"] + recs["almost_ready"] + recs["not_recommended"]
        if x["edition_id"] == str(world.ids["edition"])
    )
    assert next(c for c in item["checks"] if c["key"] == "no_exam_clash")["passed"] is False
    await client.delete(f"/api/v1/exam-windows/{w.json()['id']}", headers=admin)


async def test_teacher_can_propose_but_not_create_competitions(client, world: World) -> None:  # noqa: ANN001
    body = {"name": "New Hackathon", "discipline": "Coding", "tier": "inter_school"}
    t = await client.post("/api/v1/competitions", json=body, headers=world["teacher_a"].headers)
    assert t.json()["status"] == "proposed"
    a = await client.post(f"/api/v1/competitions/{t.json()['id']}/approve", headers=world["admin"].headers)
    assert a.json()["status"] == "active"


async def test_multi_role_user_teacher_and_parent(client, world: World) -> None:  # noqa: ANN001
    both = await make_user(
        world.tenant,
        "both@alpha.test",
        "Teacher Parent",
        [
            (Role.TEACHER, ScopeType.SQUAD, world.ids["squad_b"]),
            (Role.PARENT, ScopeType.GUARDIAN, world.ids["g_a"]),
        ],
    )
    me = (await client.get("/api/v1/auth/me", headers=both.headers)).json()
    assert {r["role"] for r in me["roles"]} == {"teacher", "parent"}
    assert {c["name"].split()[0] for c in me["children"]} == {"Ava", "Sam"}
    assert (await client.get(f"/api/v1/students/{world.ids['b1']}", headers=both.headers)).status_code == 200
    # Being a parent of Ava does not give staff access to Ava's record.
    assert (await client.get(f"/api/v1/students/{world.ids['a1']}", headers=both.headers)).status_code == 404
