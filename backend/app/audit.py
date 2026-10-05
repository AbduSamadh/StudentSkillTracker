"""Audit trail. Covers reads of student records as well as writes (spec §8.2)."""

import contextvars
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEvent


@dataclass
class RequestMeta:
    ip: str | None = None
    user_agent: str | None = None
    request_id: str | None = None


request_meta: contextvars.ContextVar[RequestMeta | None] = contextvars.ContextVar("request_meta", default=None)


def record(
    session: AsyncSession,
    *,
    actor_user_id: uuid.UUID | None,
    actor_roles: list[str] | None,
    action: str,
    subject_type: str | None = None,
    subject_id: uuid.UUID | None = None,
    reason: str | None = None,
    context: dict[str, Any] | None = None,
    tenant_id: uuid.UUID | None = None,
) -> AuditEvent:
    meta = request_meta.get() or RequestMeta()
    event = AuditEvent(
        actor_user_id=actor_user_id,
        actor_roles=actor_roles or [],
        action=action,
        subject_type=subject_type,
        subject_id=subject_id,
        reason=reason,
        ip=meta.ip,
        user_agent=(meta.user_agent or "")[:400] or None,
        request_id=meta.request_id,
        context=_jsonable(context or {}),
    )
    if tenant_id is not None:
        event.tenant_id = tenant_id
    session.add(event)
    return event


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_jsonable(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, int | float | str | bool) or value is None:
        return value
    return str(value)
