"""Test harness: a real PostgreSQL database, migrated from scratch, accessed by the
non-superuser runtime role so row-level security is exercised exactly as in production."""

import os
import tempfile

_PG = os.environ.get("STEM_TEST_PG", "localhost:5432")
os.environ.setdefault("STEM_ENVIRONMENT", "test")
os.environ.setdefault("STEM_DATABASE_URL", f"postgresql+asyncpg://stem_app:stem_app@{_PG}/stemtrack_test")
os.environ.setdefault(
    "STEM_MIGRATION_DATABASE_URL", f"postgresql+asyncpg://stem_owner:stem_owner@{_PG}/stemtrack_test"
)
os.environ.setdefault("STEM_STORAGE_LOCAL_PATH", tempfile.mkdtemp(prefix="stem-test-storage-"))
os.environ.setdefault("STEM_REDIS_URL", "redis://localhost:6379/15")

import asyncio  # noqa: E402
import uuid  # noqa: E402
from collections.abc import AsyncIterator  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from datetime import UTC, date, datetime, timedelta  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import dispose_engine, tenant_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Competition,
    CompetitionEdition,
    Consent,
    Guardian,
    RoleAssignment,
    Skill,
    SkillRequirement,
    Squad,
    SquadMembership,
    SquadTargetEdition,
    Student,
    StudentGuardian,
    User,
)
from app.models.enums import (  # noqa: E402
    Channel,
    ConsentDecision,
    ConsentPurpose,
    EntryFormat,
    Language,
    Role,
    ScopeType,
    Tier,
)
from app.security import blind_index, create_access_token, encrypt_field, hash_password  # noqa: E402

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


async def _reset_schema() -> None:
    engine = create_async_engine(get_settings().migration_database_url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.execute(text("GRANT USAGE ON SCHEMA public TO stem_app"))
    await engine.dispose()


def _migrate() -> None:
    cfg = Config(os.path.join(BACKEND, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND, "alembic"))
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session", autouse=True)
async def database() -> AsyncIterator[None]:
    await _reset_schema()
    await asyncio.to_thread(_migrate)
    yield
    await dispose_engine()


@dataclass
class Actor:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    token: str
    name: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


@dataclass
class World:
    """Two schools. In 'alpha', two teachers each coach one squad; parents and a student have
    accounts; 'beta' is a separate tenant whose data must never be reachable from alpha."""

    tenant: uuid.UUID
    beta: uuid.UUID
    actors: dict[str, Actor] = field(default_factory=dict)
    ids: dict[str, uuid.UUID] = field(default_factory=dict)

    def __getitem__(self, k: str) -> Actor:
        return self.actors[k]


async def make_user(
    tenant_id: uuid.UUID,
    email: str,
    name: str,
    roles: list[tuple[Role, ScopeType, uuid.UUID | None]],
    mfa: bool = True,
    password: str | None = None,
) -> Actor:
    async with tenant_session(tenant_id) as s:
        u = User(email=email, display_name=name, password_hash=hash_password(password) if password else None)
        s.add(u)
        await s.flush()
        for role, st, sid in roles:
            s.add(RoleAssignment(user_id=u.id, role=role, scope_type=st, scope_id=sid))
        await s.commit()
        return Actor(u.id, tenant_id, create_access_token(user_id=u.id, tenant_id=tenant_id, mfa=mfa), name)


async def build_world() -> World:
    from app.cli import provision_tenant

    alpha = await provision_tenant(f"alpha-{uuid.uuid4().hex[:6]}", "Alpha School")
    beta = await provision_tenant(f"beta-{uuid.uuid4().hex[:6]}", "Beta School")
    w = World(tenant=alpha, beta=beta)
    today = datetime.now(UTC).date()
    async with tenant_session(alpha) as s:
        from sqlalchemy import select

        skills = {k.code: k for k in (await s.scalars(select(Skill))).all()}
        comp = Competition(
            name="Robo Cup", discipline="Robotics", tier=Tier.EMIRATE, entry_format=EntryFormat.TEAM
        )
        s.add(comp)
        await s.flush()
        edition = CompetitionEdition(
            competition_id=comp.id,
            name="Robo Cup Qualifier",
            tier=Tier.EMIRATE,
            event_starts=today + timedelta(days=30),
            event_ends=today + timedelta(days=31),
            registration_opens=today - timedelta(days=5),
            registration_closes=today + timedelta(days=20),
            eligible_year_min=6,
            eligible_year_max=12,
            entry_fee=None,
            rubric=[
                {
                    "key": "programming",
                    "label": "Programming",
                    "max_score": 4,
                    "skill_code": "PROG.LOOP.02",
                    "level_if_met": 3,
                    "threshold": 0.75,
                }
            ],
        )
        s.add(edition)
        await s.flush()
        for code, lvl, wgt in (("PROG.LOOP.02", 3, 2), ("ROBO.SENS.02", 2, 1), ("COL.TEAM.01", 2, 1)):
            s.add(
                SkillRequirement(
                    competition_id=comp.id, skill_id=skills[code].id, required_level=lvl, weight=wgt
                )
            )
        sq_a = Squad(name="Squad A", discipline="Robotics")
        sq_b = Squad(name="Squad B", discipline="Robotics")
        s.add_all([sq_a, sq_b])
        await s.flush()
        s.add(SquadTargetEdition(squad_id=sq_a.id, edition_id=edition.id))

        def stu(n: str, year: int) -> Student:
            st = Student(
                external_mis_id=f"MIS-{n}", given_name=n, family_name="Test", year_group=year, gender="F"
            )
            s.add(st)
            return st

        a1, a2, b1, sib = stu("Ava", 8), stu("Ali", 9), stu("Bea", 8), stu("Sam", 7)
        await s.flush()
        for st, sq in ((a1, sq_a), (a2, sq_a), (b1, sq_b), (sib, sq_a)):
            s.add(SquadMembership(squad_id=sq.id, student_id=st.id, joined_on=today - timedelta(days=200)))

        def guardian(
            name: str, email: str, lang: Language = Language.EN, ch: Channel = Channel.EMAIL
        ) -> Guardian:
            g = Guardian(
                full_name=name,
                email_enc=encrypt_field(email),
                email_hash=blind_index(email),
                language=lang,
                preferred_channel=ch,
                whatsapp_enc=encrypt_field("+971500000000"),
            )
            s.add(g)
            return g

        g_a = guardian("Parent of Ava and Sam", "ava.parent@example.com")
        g_b = guardian("Parent of Bea", "bea.parent@example.com", Language.AR)
        g_a2 = guardian("Parent of Ali", "ali.parent@example.com")
        await s.flush()
        for st, g in ((a1, g_a), (sib, g_a), (b1, g_b), (a2, g_a2)):
            s.add(StudentGuardian(student_id=st.id, guardian_id=g.id))
        for st in (a1, b1, sib):  # a2 deliberately has no media consent
            s.add(
                Consent(
                    student_id=st.id,
                    purpose=ConsentPurpose.MEDIA,
                    decision=ConsentDecision.GRANTED,
                    decided_at=datetime.now(UTC) - timedelta(days=30),
                    method="paper",
                )
            )
        await s.commit()
        w.ids.update(
            squad_a=sq_a.id,
            squad_b=sq_b.id,
            a1=a1.id,
            a2=a2.id,
            b1=b1.id,
            sib=sib.id,
            edition=edition.id,
            competition=comp.id,
            g_a=g_a.id,
            g_b=g_b.id,
            g_a2=g_a2.id,
            skill_loop=skills["PROG.LOOP.02"].id,
            skill_sens=skills["ROBO.SENS.02"].id,
            skill_team=skills["COL.TEAM.01"].id,
        )
    w.actors["leader"] = await make_user(
        alpha, "leader@alpha.test", "Lena Leader", [(Role.LEADER, ScopeType.SCHOOL, None)]
    )
    w.actors["admin"] = await make_user(
        alpha, "admin@alpha.test", "Adam Admin", [(Role.PROGRAMME_ADMIN, ScopeType.SCHOOL, None)]
    )
    w.actors["admin_no_mfa"] = await make_user(
        alpha,
        "admin2@alpha.test",
        "Admin Without MFA",
        [(Role.PROGRAMME_ADMIN, ScopeType.SCHOOL, None)],
        mfa=False,
    )
    w.actors["teacher_a"] = await make_user(
        alpha,
        "ta@alpha.test",
        "Tara Teacher",
        [(Role.TEACHER, ScopeType.SCHOOL, None), (Role.TEACHER, ScopeType.SQUAD, w.ids["squad_a"])],
    )
    w.actors["teacher_b"] = await make_user(
        alpha,
        "tb@alpha.test",
        "Theo Teacher",
        [(Role.TEACHER, ScopeType.SCHOOL, None), (Role.TEACHER, ScopeType.SQUAD, w.ids["squad_b"])],
    )
    w.actors["parent_a"] = await make_user(
        alpha,
        "ava.parent@example.com",
        "Parent A",
        [(Role.PARENT, ScopeType.GUARDIAN, w.ids["g_a"])],
        mfa=False,
    )
    w.actors["parent_b"] = await make_user(
        alpha,
        "bea.parent@example.com",
        "Parent B",
        [(Role.PARENT, ScopeType.GUARDIAN, w.ids["g_b"])],
        mfa=False,
    )
    w.actors["student_a"] = await make_user(
        alpha, "ali@alpha.test", "Ali Student", [(Role.STUDENT, ScopeType.STUDENT, w.ids["a2"])], mfa=False
    )
    async with tenant_session(beta) as s:
        bs = Student(external_mis_id="MIS-BETA", given_name="Beta", family_name="Kid", year_group=8)
        s.add(bs)
        await s.commit()
        w.ids["beta_student"] = bs.id
    w.actors["beta_leader"] = await make_user(
        beta, "leader@beta.test", "Beta Leader", [(Role.LEADER, ScopeType.SCHOOL, None)]
    )
    return w


@pytest.fixture(scope="session")
async def world(database: None) -> World:
    return await build_world()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def ymd(days: int = 0) -> str:
    return (datetime.now(UTC).date() + timedelta(days=days)).isoformat()


__all__ = ["Actor", "World", "make_user", "ymd", "date"]
