"""Fixed-window rate limiting in Redis, with an in-process fallback when Redis is down."""

import logging
import time
from collections import defaultdict

from fastapi import HTTPException, Request, status
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import get_settings

log = logging.getLogger(__name__)
_redis: Redis | None = None
_local: dict[str, tuple[int, int]] = defaultdict(lambda: (0, 0))


def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(get_settings().redis_url, socket_connect_timeout=0.5, socket_timeout=0.5)
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
    _redis = None


async def hit(key: str, limit: int, window_seconds: int = 60) -> bool:
    """Returns True if the call is allowed."""
    window = int(time.time() // window_seconds)
    bucket = f"rl:{key}:{window}"
    try:
        r = get_redis()
        pipe = r.pipeline()
        pipe.incr(bucket)
        pipe.expire(bucket, window_seconds + 1)
        count, _ = await pipe.execute()
        return int(count) <= limit
    except (RedisError, OSError):
        log.warning("rate limiter falling back to in-process counters")
        w, count = _local[key]
        if w != window:
            w, count = window, 0
        count += 1
        _local[key] = (w, count)
        return count <= limit


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def limit_auth(request: Request) -> None:
    settings = get_settings()
    if settings.environment == "test":
        return
    key = f"auth:{client_ip(request)}:{request.url.path}"
    if not await hit(key, settings.rate_limit_auth_per_minute):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Try again in a minute.")
