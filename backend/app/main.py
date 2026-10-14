import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.v1 import (
    admin,
    auth,
    capture,
    competitions,
    consents,
    home,
    inbound,
    messages,
    operations,
    portal,
    reports,
    skills,
    squads,
    students,
)
from app.config import get_settings
from app.db import anonymous_session, dispose_engine
from app.logging_setup import configure_logging
from app.ratelimit import close_redis, get_redis
from app.services import storage

log = logging.getLogger("stemtrack.http")


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    settings = get_settings()
    settings.assert_safe_for_production()
    configure_logging()
    if settings.sentry_dsn:
        import sentry_sdk

        # send_default_pii=False: request bodies and user details never leave for Sentry.
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            send_default_pii=False,
            traces_sample_rate=0.1,
        )
    if settings.otel_enabled:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    yield
    await dispose_engine()
    await close_redis()


app = FastAPI(
    title="School STEM Competition & Talent Platform",
    version="1.0.0",
    openapi_url="/api/v1/openapi.json",
    docs_url="/api/docs",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With", "X-Request-Id", "Idempotency-Key"],
    expose_headers=["Idempotent-Replay", "X-Sync-Conflict", "X-Request-Id"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):  # noqa: ANN001, ANN201
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("unhandled error", extra={"request_id": rid, "path": request.url.path})
        response = JSONResponse({"detail": "Internal error", "request_id": rid}, status_code=500)
    response.headers["X-Request-Id"] = rid
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cache-Control"] = response.headers.get("Cache-Control", "no-store")
    if get_settings().environment in ("production", "staging"):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    log.info(
        "request",
        extra={
            "request_id": rid,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": round((time.perf_counter() - start) * 1000, 1),
        },
    )
    return response


for r in (
    auth,
    home,
    students,
    competitions,
    squads,
    capture,
    skills,
    messages,
    consents,
    portal,
    reports,
    operations,
    admin,
    inbound,
):
    app.include_router(r.router, prefix="/api/v1")


@app.get("/api/v1/health", tags=["ops"])
async def health() -> JSONResponse:
    """Database, Redis and object storage are checked independently."""
    checks: dict[str, str] = {}
    try:
        async with anonymous_session() as s:
            await s.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {type(exc).__name__}"
    try:
        await get_redis().ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {type(exc).__name__}"
    checks["object_storage"] = "ok" if await storage.healthy() else "error"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {"status": "ok" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503
    )
