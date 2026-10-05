"""Background jobs.

Request handlers call ``ctx.defer(name, **kwargs)``; deferred jobs run only after the
request transaction commits. With STEM_USE_JOB_QUEUE=true they are enqueued to ARQ
(Redis); otherwise they run inline after commit (tests and single-process development).
"""

import importlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from app.config import get_settings

log = logging.getLogger(__name__)

# name -> "module:function". Each job takes (tenant_id: str, **kwargs).
JOBS: dict[str, str] = {
    "deliver_webhooks": "app.services.webhooks:deliver_pending",
    "dispatch_message": "app.services.messaging.dispatch:dispatch_message_job",
    "generate_report": "app.services.reporting.jobs:generate_report_job",
    "refresh_flags": "app.services.jobs_nightly:refresh_flags_job",
}

_pool = None


def resolve(name: str) -> Callable[..., Awaitable[Any]]:
    module, func = JOBS[name].split(":")
    return getattr(importlib.import_module(module), func)


async def _arq_pool():  # noqa: ANN202
    global _pool
    if _pool is None:
        from arq import create_pool
        from arq.connections import RedisSettings

        _pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    return _pool


async def run_or_enqueue(name: str, tenant_id: uuid.UUID, **kwargs: Any) -> None:
    kwargs = {k: str(v) if isinstance(v, uuid.UUID) else v for k, v in kwargs.items()}
    if get_settings().use_job_queue:
        pool = await _arq_pool()
        await pool.enqueue_job("run_job", name, str(tenant_id), kwargs)
        return
    try:
        await resolve(name)(str(tenant_id), **kwargs)
    except Exception:  # inline jobs must never break the request that deferred them
        log.exception("inline job %s failed", name)
