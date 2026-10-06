"""Operator CLI.

python -m app.cli provision-tenant --slug my-school --name "My School" [--name-ar ...]
python -m app.cli create-user --tenant my-school --email head@school.ae --name "Head" --role leader
python -m app.cli seed-demo
python -m app.cli totp --secret BASE32SECRET
python -m app.cli purge --tenant my-school
"""

import argparse
import asyncio
import getpass
import uuid

import pyotp
from cryptography.fernet import InvalidToken
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.db import anonymous_session, dispose_engine, tenant_session
from app.models import RoleAssignment, Tenant, User
from app.models.enums import Role, ScopeType
from app.security import hash_password
from app.seed.reference import load_reference_data
from app.tenancy import TenantSettings


async def provision_tenant(slug: str, name: str, name_ar: str | None = None) -> uuid.UUID:
    """Tenants are created by the owner role; the runtime role cannot insert into tenants."""
    async with anonymous_session() as s:
        existing = await s.scalar(select(Tenant).where(Tenant.slug == slug))
    if existing is not None:
        tid = existing.id
    else:
        tid = uuid.uuid4()
        engine = create_async_engine(get_settings().migration_database_url)
        async with engine.begin() as conn:
            await conn.execute(
                insert(Tenant).values(
                    id=tid,
                    slug=slug,
                    name=name,
                    name_ar=name_ar,
                    settings=TenantSettings().model_dump(mode="json"),
                )
            )
        await engine.dispose()
    async with tenant_session(tid) as session:
        counts = await load_reference_data(session)
        await session.commit()
    print(f"tenant {slug} ({tid}) ready: {counts}")
    return tid


async def create_user(tenant: str, email: str, name: str, roles: list[str], password: str | None) -> None:
    async with anonymous_session() as s:
        t = await s.scalar(select(Tenant).where(Tenant.slug == tenant))
    if t is None:
        raise SystemExit(f"unknown tenant {tenant}")
    async with tenant_session(t.id) as session:
        u = await session.scalar(select(User).where(User.email == email.lower()))
        if u is None:
            u = User(email=email.lower(), display_name=name)
            session.add(u)
            await session.flush()
        if password:
            u.password_hash = hash_password(password)
        for r in roles:
            session.add(RoleAssignment(user_id=u.id, role=Role(r), scope_type=ScopeType.SCHOOL))
        await session.commit()
    print(f"user {email} ready with roles {roles}")


async def purge(tenant: str) -> None:
    from app.services.jobs_nightly import retention_job

    async with anonymous_session() as s:
        t = await s.scalar(select(Tenant).where(Tenant.slug == tenant))
    if t is None:
        raise SystemExit(f"unknown tenant {tenant}")
    print(await retention_job(str(t.id)))


async def rotate_keys() -> None:
    """Re-encrypt every tenant's encrypted fields under the current key. Each tenant commits on
    its own; one that fails (a value no configured key can read) is reported and left as it was."""
    from app.services.keys import rotate_tenant

    async with anonymous_session() as s:
        tenants = [(t.id, t.slug) for t in (await s.scalars(select(Tenant))).all()]
    failed = []
    for tid, slug in tenants:
        async with tenant_session(tid) as session:
            tenant = await session.get(Tenant, tid)
            assert tenant is not None
            try:
                counts = await rotate_tenant(session, tenant)
            except InvalidToken:
                await session.rollback()
                failed.append(slug)
                print(f"{slug}: FAILED — a value could not be read with any configured key; nothing changed")
                continue
            await session.commit()
        print(f"{slug}: re-encrypted {counts}")
    if failed:
        raise SystemExit(f"rotation incomplete for: {', '.join(failed)}. Keep the old key configured.")


def main() -> None:
    p = argparse.ArgumentParser(prog="stemtrack")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("provision-tenant")
    a.add_argument("--slug", required=True)
    a.add_argument("--name", required=True)
    a.add_argument("--name-ar")
    b = sub.add_parser("create-user")
    b.add_argument("--tenant", required=True)
    b.add_argument("--email", required=True)
    b.add_argument("--name", required=True)
    b.add_argument("--role", action="append", choices=[r.value for r in Role], default=[])
    b.add_argument("--password", action="store_true", help="prompt for a password")
    sub.add_parser("seed-demo")
    c = sub.add_parser("totp")
    c.add_argument("--secret", required=True)
    d = sub.add_parser("purge")
    d.add_argument("--tenant", required=True)
    sub.add_parser("rotate-keys", help="re-encrypt stored secrets under the current key")
    args = p.parse_args()

    async def run() -> None:
        try:
            if args.cmd == "provision-tenant":
                await provision_tenant(args.slug, args.name, args.name_ar)
            elif args.cmd == "create-user":
                pw = getpass.getpass("Password: ") if args.password else None
                await create_user(args.tenant, args.email, args.name, args.role, pw)
            elif args.cmd == "seed-demo":
                from app.seed.demo import seed_demo

                await seed_demo()
            elif args.cmd == "purge":
                await purge(args.tenant)
            elif args.cmd == "rotate-keys":
                await rotate_keys()
        finally:
            await dispose_engine()

    if args.cmd == "totp":
        print(pyotp.TOTP(args.secret).now())
        return
    asyncio.run(run())


if __name__ == "__main__":
    main()
