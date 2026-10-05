"""Database engine and tenant-scoped sessions.

Isolation between schools is enforced by PostgreSQL row-level security, not by
application filters. Every transaction opened through ``tenant_session`` runs
``set_config('app.tenant_id', ..., true)`` first, and every tenant table carries a
policy comparing ``tenant_id`` with that setting. The runtime database role has no
BYPASSRLS, so a forgotten ``WHERE tenant_id = ...`` cannot leak another school's rows.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database_url, pool_pre_ping=True, pool_size=10, max_overflow=20)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


@event.listens_for(Session, "after_begin")
def _apply_tenant_context(session: Session, transaction, connection) -> None:  # noqa: ANN001
    """Pin every transaction to the session's tenant before any statement runs."""
    tenant_id = session.info.get("tenant_id")
    if tenant_id is not None:
        connection.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_id)})


@event.listens_for(Session, "before_flush")
def _stamp_tenant_id(session: Session, flush_context, instances) -> None:  # noqa: ANN001
    tenant_id = session.info.get("tenant_id")
    if tenant_id is None:
        return
    for obj in session.new:
        if hasattr(obj, "tenant_id") and getattr(obj, "tenant_id", None) is None:
            obj.tenant_id = tenant_id


@asynccontextmanager
async def tenant_session(tenant_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    session = get_sessionmaker()()
    session.info["tenant_id"] = tenant_id
    try:
        yield session
    finally:
        await session.close()


@asynccontextmanager
async def anonymous_session() -> AsyncIterator[AsyncSession]:
    """A session with no tenant context: RLS hides every tenant row.

    Used only to resolve a tenant by slug before login.
    """
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        await session.close()
