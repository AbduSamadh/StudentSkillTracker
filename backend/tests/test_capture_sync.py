"""Offline capture and the sync contract (acceptance criterion 13.1.1)."""

import uuid

from sqlalchemy import func, select

from app.db import tenant_session
from app.models import Attendance, Result, TrainingSession
from tests.conftest import World, ymd


async def test_full_competition_day_replayed_twice_has_no_duplicates(client, world: World) -> None:  # noqa: ANN001
    """Simulate a phone at a venue with no wifi: the queue replays every write twice
    (e.g. the first responses were lost). The server must end with exactly one of each."""
    h = world["teacher_a"].headers
    sq, a1, a2 = str(world.ids["squad_a"]), str(world.ids["a1"]), str(world.ids["a2"])
    session_id = str(uuid.uuid4())
    queue = [
        (
            "POST",
            "/api/v1/sessions",
            {
                "id": session_id,
                "idempotency_key": f"s-{session_id}",
                "squad_id": sq,
                "starts_at": f"{ymd()}T07:30:00Z",
                "location": "Venue warm-up",
                "client_modified_at": f"{ymd()}T07:31:00Z",
            },
        ),
        (
            "POST",
            f"/api/v1/sessions/{session_id}/attendance",
            {
                "records": [
                    {"student_id": a1, "status": "present", "client_modified_at": f"{ymd()}T07:35:00Z"},
                    {"student_id": a2, "status": "late", "client_modified_at": f"{ymd()}T07:35:00Z"},
                ]
            },
        ),
        (
            "POST",
            "/api/v1/results",
            {
                "idempotency_key": "day-result-1",
                "edition_id": str(world.ids["edition"]),
                "squad_id": sq,
                "participant_ids": [a1, a2],
                "placement": 3,
                "field_size": 24,
                "rubric_scores": {"programming": 4},
                "client_modified_at": f"{ymd()}T16:00:00Z",
            },
        ),
        (
            "POST",
            "/api/v1/results",
            {
                "idempotency_key": "day-result-2",
                "edition_id": str(world.ids["edition"]),
                "squad_id": sq,
                "participant_ids": [a2],
                "placement": 7,
                "field_size": None,
                "client_modified_at": f"{ymd()}T16:05:00Z",
            },
        ),
    ]
    first, second = [], []
    for attempt in (first, second):
        for method, url, body in queue:
            r = await client.request(method, url, json=body, headers=h)
            assert r.status_code in (200, 201), (url, r.text)
            attempt.append(r.json())
    # Replays return the canonical record.
    assert first[0]["id"] == second[0]["id"] == session_id
    assert first[2]["id"] == second[2]["id"]
    assert second[2]["performance_index"] is not None
    assert second[3]["data_quality_flags"] == ["missing_field_size"]
    async with tenant_session(world.tenant) as s:
        assert (
            await s.scalar(
                select(func.count())
                .select_from(TrainingSession)
                .where(TrainingSession.id == uuid.UUID(session_id))
            )
            == 1
        )
        assert (
            await s.scalar(
                select(func.count())
                .select_from(Attendance)
                .where(Attendance.session_id == uuid.UUID(session_id))
            )
            == 2
        )
        assert (
            await s.scalar(
                select(func.count())
                .select_from(Result)
                .where(Result.idempotency_key.in_(["day-result-1", "day-result-2"]))
            )
            == 2
        )


async def test_replay_response_is_marked(client, world: World) -> None:  # noqa: ANN001
    body = {
        "idempotency_key": "replay-mark",
        "squad_id": str(world.ids["squad_a"]),
        "starts_at": f"{ymd()}T10:00:00Z",
    }
    r1 = await client.post("/api/v1/sessions", json=body, headers=world["teacher_a"].headers)
    r2 = await client.post("/api/v1/sessions", json=body, headers=world["teacher_a"].headers)
    assert r1.status_code == 201 and r2.status_code == 200
    assert r2.headers["Idempotent-Replay"] == "true"


async def test_attendance_last_write_wins(client, world: World) -> None:  # noqa: ANN001
    h = world["teacher_a"].headers
    s = (
        await client.post(
            "/api/v1/sessions",
            json={"squad_id": str(world.ids["squad_a"]), "starts_at": f"{ymd()}T15:00:00Z"},
            headers=h,
        )
    ).json()
    a1 = str(world.ids["a1"])
    url = f"/api/v1/sessions/{s['id']}/attendance"
    newer = {"records": [{"student_id": a1, "status": "absent", "client_modified_at": f"{ymd()}T15:10:00Z"}]}
    older = {"records": [{"student_id": a1, "status": "present", "client_modified_at": f"{ymd()}T15:05:00Z"}]}
    await client.post(url, json=newer, headers=h)
    r = await client.post(url, json=older, headers=h)  # arrives late from another phone
    assert r.headers.get("X-Sync-Conflict") == "stale:1"
    assert r.json()[0]["status"] == "absent"


async def test_released_result_needs_admin_override_and_is_logged(client, world: World) -> None:  # noqa: ANN001
    res = (
        await client.post(
            "/api/v1/results",
            json={
                "edition_id": str(world.ids["edition"]),
                "squad_id": str(world.ids["squad_a"]),
                "participant_ids": [str(world.ids["a1"])],
                "placement": 2,
                "field_size": 12,
            },
            headers=world["teacher_a"].headers,
        )
    ).json()
    rel = await client.post(f"/api/v1/results/{res['id']}/release", headers=world["admin"].headers)
    assert rel.status_code == 200 and rel.json()["released_at"]
    blocked = await client.patch(
        f"/api/v1/results/{res['id']}", json={"placement": 1}, headers=world["teacher_a"].headers
    )
    assert blocked.status_code == 409
    no_reason = await client.patch(
        f"/api/v1/results/{res['id']}",
        json={"placement": 1, "admin_override": True},
        headers=world["admin"].headers,
    )
    assert no_reason.status_code == 422
    ok = await client.patch(
        f"/api/v1/results/{res['id']}",
        json={
            "placement": 1,
            "admin_override": True,
            "override_reason": "Organiser corrected the published table",
        },
        headers=world["admin"].headers,
    )
    assert ok.status_code == 200 and ok.json()["placement"] == 1
    audit = (
        await client.get("/api/v1/audit?action=result.admin_override", headers=world["leader"].headers)
    ).json()
    assert any(e["reason"] == "Organiser corrected the published table" for e in audit["items"])


async def test_index_endpoint_explains_itself(client, world: World) -> None:  # noqa: ANN001
    res = (
        await client.post(
            "/api/v1/results",
            json={
                "edition_id": str(world.ids["edition"]),
                "squad_id": str(world.ids["squad_a"]),
                "participant_ids": [str(world.ids["a1"])],
                "placement": 1,
                "field_size": 10,
            },
            headers=world["teacher_a"].headers,
        )
    ).json()
    idx = (await client.get(f"/api/v1/results/{res['id']}/index", headers=world["teacher_a"].headers)).json()
    assert idx["components"]["field_size"] == 10 and idx["tier"] == "emirate"
    assert idx["performance_index"] == 69.97  # 83.30 at emirate tier weight 0.84
