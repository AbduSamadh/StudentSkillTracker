"""Tenant isolation is enforced by PostgreSQL, not by application filters."""

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.db import anonymous_session, tenant_session
from app.models import Base, Student


async def test_every_tenant_table_has_forced_rls_and_a_policy() -> None:
    tenant_tables = sorted(t.name for t in Base.metadata.sorted_tables if "tenant_id" in t.c)
    async with anonymous_session() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                    "(SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname) AS policies "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind = 'r'"
                )
            )
        ).all()
    info = {r.relname: r for r in rows}
    missing = [
        t
        for t in tenant_tables
        if not (info[t].relrowsecurity and info[t].relforcerowsecurity and info[t].policies)
    ]
    assert missing == [], f"tables without forced RLS: {missing}"


async def test_runtime_role_cannot_bypass_rls() -> None:
    async with anonymous_session() as s:
        row = (
            await s.execute(text("SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = current_user"))
        ).one()
    assert row.rolbypassrls is False and row.rolsuper is False


async def test_no_tenant_context_sees_nothing(world) -> None:  # noqa: ANN001
    async with anonymous_session() as s:
        assert (await s.scalars(select(Student))).all() == []


async def test_tenant_context_hides_other_tenant(world) -> None:  # noqa: ANN001
    async with tenant_session(world.tenant) as s:
        ids = set(await s.scalars(select(Student.id)))
    assert world.ids["a1"] in ids
    assert world.ids["beta_student"] not in ids
    async with tenant_session(world.beta) as s:
        assert await s.get(Student, world.ids["a1"]) is None


async def test_cannot_write_into_another_tenant(world) -> None:  # noqa: ANN001
    async with tenant_session(world.tenant) as s:
        s.add(
            Student(
                tenant_id=world.beta, external_mis_id="X-CROSS", given_name="X", family_name="Y", year_group=5
            )
        )
        with pytest.raises(DBAPIError):
            await s.flush()
        await s.rollback()


async def test_cannot_update_other_tenant_rows_even_with_raw_sql(world) -> None:  # noqa: ANN001
    async with tenant_session(world.tenant) as s:
        res = await s.execute(
            text("UPDATE students SET given_name = 'pwned' WHERE id = :id"), {"id": world.ids["beta_student"]}
        )
        assert res.rowcount == 0
        await s.rollback()


async def test_audit_log_is_append_only(world) -> None:  # noqa: ANN001
    async with tenant_session(world.tenant) as s:
        await s.execute(
            text(
                "INSERT INTO audit_events (id, tenant_id, actor_roles, action, context) "
                "VALUES (:id, app_current_tenant(), '{}', 'test.event', '{}')"
            ),
            {"id": uuid.uuid4()},
        )
        await s.commit()
    for stmt in ("UPDATE audit_events SET action = 'tampered'", "DELETE FROM audit_events"):
        async with tenant_session(world.tenant) as s:
            with pytest.raises(ProgrammingError):
                await s.execute(text(stmt))
            await s.rollback()


async def test_recent_audit_events_cannot_be_purged(world) -> None:  # noqa: ANN001
    async with tenant_session(world.tenant) as s:
        with pytest.raises(DBAPIError):
            await s.execute(text("SELECT purge_audit_events(now() - interval '30 days')"))
        await s.rollback()


async def test_runtime_role_cannot_create_tenants() -> None:
    async with anonymous_session() as s:
        with pytest.raises(ProgrammingError):
            await s.execute(
                text("INSERT INTO tenants (id, slug, name, settings) VALUES (:i, 'x', 'x', '{}')"),
                {"i": uuid.uuid4()},
            )
        await s.rollback()
