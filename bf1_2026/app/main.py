"""FastAPI application entrypoint."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.config import Environment, settings
from app.routers import admin, bets, drivers, predictions, races
from app.utils.security import startup_security_report


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("BF1-2026 Quantitative System starting up")

    # Say plainly, at boot, what is not configured — rather than letting it be
    # discovered by whoever finds the open endpoint first.
    for problem in startup_security_report():
        logger.warning(f"SECURITY: {problem}")

    # Schema is owned by Alembic (`alembic upgrade head`). create_all is only
    # a convenience for local development — running it in production would
    # build a schema Alembic then thinks it has never migrated.
    #
    # The old version wrapped this in `except Exception: logger.warning(...)`,
    # so a database that was unreachable or had no tables produced one warning
    # and a fully "healthy" app in which every request then failed.
    import app.models  # noqa: F401  (register all ORM models on Base)

    if settings.environment == Environment.development:
        try:
            from app.database import init_db

            await init_db()
            logger.info("Database tables ensured (development create_all)")
        except Exception as e:
            logger.error(f"Database init failed: {e}")
    else:
        try:
            from sqlalchemy import text

            from app.database import engine

            async with engine.connect() as conn:
                revision = (
                    await conn.execute(text("SELECT version_num FROM alembic_version"))
                ).scalar_one_or_none()
            logger.info(f"Database reachable, schema at revision {revision}")
        except Exception as e:
            logger.error(
                f"Database schema check failed: {e}. "
                "Run `alembic upgrade head` — the API cannot serve data until "
                "the schema exists."
            )

    # The scheduler runs in its own container (`python -m app.scheduler.run`,
    # the bf1_scheduler service). Starting it here as well made every job fire
    # twice — duplicate upstream fetches, duplicate ingestion, and two
    # concurrent retrains writing the same models_storage volume. Set
    # RUN_SCHEDULER_IN_API=true only for a single-process deployment that has
    # no separate scheduler container.
    run_scheduler = os.getenv("RUN_SCHEDULER_IN_API", "false").lower() == "true"
    if run_scheduler:
        try:
            from app.scheduler.jobs import start_scheduler
            start_scheduler()
            logger.info("Scheduler started in-process (RUN_SCHEDULER_IN_API)")
        except Exception as e:
            logger.warning(f"Scheduler failed to start: {e}")
    else:
        logger.info("Scheduler not started here; the dedicated container owns it")

    yield

    # Shutdown
    if run_scheduler:
        try:
            from app.scheduler.jobs import stop_scheduler
            stop_scheduler()
        except Exception:
            pass
    logger.info("BF1-2026 Quantitative System shut down")


# Swagger/ReDoc publish the whole API surface — including /api/v1/admin/* — to
# anonymous visitors, so they are off unless explicitly enabled or running in
# development. Passing None to these arguments unregisters the routes.
_docs_on = settings.enable_docs or settings.environment == Environment.development

app = FastAPI(
    title="BF1-2026 Quantitative System",
    version="1.0.0",
    description="Quantitative predictive analysis engine for BF1-2026",
    lifespan=lifespan,
    docs_url="/docs" if _docs_on else None,
    redoc_url="/redoc" if _docs_on else None,
    openapi_url="/openapi.json" if _docs_on else None,
)

# CORS. Entries are stripped because "a.com, b.com" otherwise yields a second
# origin with a leading space that can never match. A wildcard combined with
# allow_credentials makes Starlette echo back any requesting origin, so the two
# are mutually exclusive: an explicit list gets credentials, "*" does not.
origins = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
allow_credentials = origins != ["*"]
if not allow_credentials:
    logger.warning(
        "ALLOWED_ORIGINS is '*' — disabling credentialed CORS. Set an explicit "
        "origin list to allow cookies or Authorization from a browser."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(races.router)
app.include_router(drivers.router)
app.include_router(predictions.router)
app.include_router(bets.router)
app.include_router(admin.router)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "bf1-2026-quantitative-system",
        "version": "1.0.0",
        "environment": settings.environment.value,
    }


@app.get("/")
async def root():
    """Root redirect to docs."""
    return {
        "message": "BF1-2026 Quantitative System API",
        "docs": "/docs",
        "health": "/health",
    }
