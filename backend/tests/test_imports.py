"""MIS import: dry-run never writes; commit is idempotent; matched on MIS ID, not name."""

import json

from sqlalchemy import func, select

from app.db import tenant_session
from app.models import Guardian, Student
from app.security import decrypt_field
from tests.conftest import World

CSV = """Pupil ID,Forename,Surname,DOB,NC Year,Gender,Contact 1 Name,Contact 1 Email,Contact 1 Language,Contact 1 Channel
IMP-001,Layla,Haddad,2013-04-02,Year 8,F,Rana Haddad,rana@example.org,Arabic,whatsapp
IMP-002,Omar,Haddad,15/09/2015,6,M,Rana Haddad,rana@example.org,Arabic,whatsapp
IMP-003,Chloe,Smith,2012-01-20,9,F,Tom Smith,tom@example.org,English,email
"""


async def _dry(client, world: World, csv: str, mapping: dict | None = None, leavers: bool = False) -> dict:  # noqa: ANN001
    r = await client.post(
        "/api/v1/imports/mis/dry-run",
        files={"file": ("roster.csv", csv.encode(), "text/csv")},
        data={"mapping": json.dumps(mapping or {}), "mark_missing_as_left": str(leavers).lower()},
        headers=world["admin"].headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_dry_run_previews_without_writing_then_commit_applies(client, world: World) -> None:  # noqa: ANN001
    batch = await _dry(client, world, CSV)
    assert batch["summary"]["creates"] == 3 and batch["summary"]["errors"] == 0
    assert batch["mapping"]["columns"]["external_mis_id"] == "Pupil ID"
    async with tenant_session(world.tenant) as s:
        assert (
            await s.scalar(
                select(func.count()).select_from(Student).where(Student.external_mis_id.like("IMP-%"))
            )
            == 0
        )
    c = await client.post(
        "/api/v1/imports/mis/commit", json={"batch_id": batch["id"]}, headers=world["admin"].headers
    )
    assert c.status_code == 200 and c.json()["status"] == "committed"
    async with tenant_session(world.tenant) as s:
        rows = (await s.scalars(select(Student).where(Student.external_mis_id.like("IMP-%")))).all()
        assert {r.year_group for r in rows} == {8, 6, 9}
        rana = (await s.scalars(select(Guardian).where(Guardian.full_name == "Rana Haddad"))).all()
        assert len(rana) == 1, "siblings share one guardian record"
        assert decrypt_field(rana[0].email_enc) == "rana@example.org"
        assert rana[0].language.value == "ar" and rana[0].preferred_channel.value == "whatsapp"
    again = await client.post(
        "/api/v1/imports/mis/commit", json={"batch_id": batch["id"]}, headers=world["admin"].headers
    )
    assert again.status_code == 200  # committing twice is a no-op


async def test_reimport_is_idempotent_and_matches_on_mis_id_not_name(client, world: World) -> None:  # noqa: ANN001
    same = await _dry(client, world, CSV)
    assert (
        same["summary"]["creates"] == 0
        and same["summary"]["updates"] == 0
        and same["summary"]["unchanged"] == 3
    )
    renamed = CSV.replace("IMP-003,Chloe,Smith", "IMP-003,Chloé,Smith-Jones").replace(
        "2012-01-20,9", "2012-01-20,10"
    )
    b = await _dry(client, world, renamed)
    assert b["summary"]["creates"] == 0 and b["summary"]["updates"] == 1
    assert b["diff"]["updates"][0]["changes"]["year_group"] == {"from": 9, "to": 10}


async def test_stale_preview_cannot_be_committed(client, world: World) -> None:  # noqa: ANN001
    first = await _dry(
        client, world, CSV.replace("IMP-003,Chloe,Smith,2012-01-20,9", "IMP-003,Chloe,Smith,2012-01-20,11")
    )
    second = await _dry(
        client, world, CSV.replace("IMP-003,Chloe,Smith,2012-01-20,9", "IMP-003,Chloe,Smith,2012-01-20,12")
    )
    assert (
        await client.post(
            "/api/v1/imports/mis/commit", json={"batch_id": second["id"]}, headers=world["admin"].headers
        )
    ).status_code == 200
    stale = await client.post(
        "/api/v1/imports/mis/commit", json={"batch_id": first["id"]}, headers=world["admin"].headers
    )
    assert stale.status_code == 409


async def test_validation_errors_block_commit(client, world: World) -> None:  # noqa: ANN001
    bad = CSV + "IMP-004,,Nobody,2012-13-40,Year 15,F,,,,\nIMP-001,Dup,Row,2013-04-02,8,F,,,,\n"
    b = await _dry(client, world, bad)
    rows = {e["row"]: e["errors"] for e in b["diff"]["errors"]}
    assert any("given_name is empty" in x for x in rows[5])
    assert any("duplicate" in x for x in rows[6])
    r = await client.post(
        "/api/v1/imports/mis/commit", json={"batch_id": b["id"]}, headers=world["admin"].headers
    )
    assert r.status_code == 422


async def test_leavers_are_only_marked_when_asked(client, world: World) -> None:  # noqa: ANN001
    only_two = "\n".join(CSV.strip().splitlines()[:3]) + "\n"
    b = await _dry(client, world, only_two)
    assert b["summary"]["leavers"] == 0
    b = await _dry(client, world, only_two, leavers=True)
    assert "IMP-003" in {x["external_mis_id"] for x in b["diff"]["leavers"]}
