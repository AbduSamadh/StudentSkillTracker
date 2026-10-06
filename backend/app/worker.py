"""ARQ worker: background jobs and schedules.

Run with:  arq app.worker.WorkerSettings
"""

import logging

from arq import cron
from arq.connections import RedisSettings

from app.config import get_settings
from app.jobs import resolve
from app.logging_setup import configure_logging
from app.services.jobs_nightly import insights_job, refresh_flags_job, retention_job, tenant_ids
from app.services.messaging.dispatch import dispatch_due
from app.services.webhooks import deliver_pending

log = logging.getLogger(__name__)


async def run_job(ctx: dict, name: str, tenant_id: str, kwargs: dict) -> object:
    return await resolve(name)(tenant_id, **kwargs)


async def _each_tenant(fn, label: str) -> None:  # noqa: ANN001
    for tid in await tenant_ids():
        try:
            await fn(str(tid))
        except Exception:
            log.exception("%s failed for tenant %s", label, tid)


async def every_minute(ctx: dict) -> None:
    await _each_tenant(dispatch_due, "dispatch")
    await _each_tenant(deliver_pending, "webhooks")


async def nightly(ctx: dict) -> None:
    await _each_tenant(refresh_flags_job, "flags")
    await _each_tenant(retention_job, "retention")


async def half_termly_insights(ctx: dict) -> None:
    await _each_tenant(insights_job, "insights")


async def startup(ctx: dict) -> None:
    configure_logging()


class WorkerSettings:
    functions = [run_job]
    cron_jobs = [
        cron(every_minute, second=0),
        cron(nightly, hour=1, minute=30),  # ~05:30 Gulf time
        # Insights are drafted weekly so teachers can review through the half-term.
        cron(half_termly_insights, weekday="sun", hour=2, minute=0),
    ]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 10
    job_timeout = 600


__all__ = ["WorkerSettings"]
