"""The home page's to-do counts follow the same capability and scope rules as the pages they
link to: a teacher is never told about another squad's work, and never sees counts for decisions
that are not theirs to make."""

from tests.conftest import World


async def _home(client, world: World, who: str) -> dict:  # noqa: ANN001
    r = await client.get("/api/v1/home", headers=world[who].headers)
    assert r.status_code == 200, r.text
    return r.json()


async def test_counts_are_scoped_to_what_the_caller_can_reach(client, world: World) -> None:  # noqa: ANN001
    before_a = (await _home(client, world, "teacher_a"))["counts"]["skills_to_approve"]
    before_b = (await _home(client, world, "teacher_b"))["counts"]["skills_to_approve"]
    before_admin = (await _home(client, world, "admin"))["counts"]["skills_to_approve"]

    # A student in squad A proposes a skill; it waits for a teacher's countersign.
    r = await client.post(
        "/api/v1/skills/awards/self-assess",
        json={"student_id": str(world.ids["a2"]), "skill_id": str(world.ids["skill_team"]), "level": 2},
        headers=world["student_a"].headers,
    )
    assert r.status_code == 201, r.text

    assert (await _home(client, world, "teacher_a"))["counts"]["skills_to_approve"] == before_a + 1
    assert (await _home(client, world, "teacher_b"))["counts"]["skills_to_approve"] == before_b
    assert (await _home(client, world, "admin"))["counts"]["skills_to_approve"] == before_admin + 1

    # The count matches the list the home page links to.
    listed = await client.get("/api/v1/skills/awards/proposed", headers=world["teacher_a"].headers)
    assert len(listed.json()) == before_a + 1


async def test_only_decisions_the_caller_can_make_are_counted(client, world: World) -> None:  # noqa: ANN001
    teacher = (await _home(client, world, "teacher_a"))["counts"]
    leader = (await _home(client, world, "leader"))["counts"]
    admin = (await _home(client, world, "admin"))["counts"]

    teacher_work = {"skills_to_approve", "students_to_check", "notes_to_review", "drafts_awaiting_release"}
    not_teacher_work = {"messages_to_release", "spend_to_approve", "kit_requests", "competitions_to_approve"}
    assert teacher_work <= set(teacher)
    assert not not_teacher_work & set(teacher)
    assert {"messages_to_release", "spend_to_approve", "templates_to_approve"} <= set(leader)
    assert "skills_to_approve" not in leader  # leaders don't verify skills
    assert {"kit_requests", "competitions_to_approve", "messages_to_release"} <= set(admin)
    assert "spend_to_approve" not in admin  # only leaders approve spend


async def test_squads_show_members_and_the_next_event(client, world: World) -> None:  # noqa: ANN001
    squads = (await _home(client, world, "teacher_a"))["squads"]
    assert [s["name"] for s in squads] == ["Squad A"]  # never another teacher's squad
    a = squads[0]
    assert a["member_count"] >= 3
    assert a["next_event"]["name"] == "Robo Cup Qualifier"

    whole_school = {s["name"] for s in (await _home(client, world, "leader"))["squads"]}
    assert {"Squad A", "Squad B"} <= whole_school
    b = next(s for s in (await _home(client, world, "leader"))["squads"] if s["name"] == "Squad B")
    assert b["next_event"] is None


async def test_families_and_other_schools_cannot_use_it(client, world: World) -> None:  # noqa: ANN001
    for who in ("parent_a", "student_a"):
        r = await client.get("/api/v1/home", headers=world[who].headers)
        assert r.status_code == 403
    beta = await client.get("/api/v1/home", headers=world["beta_leader"].headers)
    assert beta.status_code == 200
    assert not {"Squad A", "Squad B"} & {s["name"] for s in beta.json()["squads"]}
