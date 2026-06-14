"""FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.config import settings
from app.routers import admin, bets, drivers, predictions, races


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("BF1-2026 Quantitative System starting up")

    # Ensure database tables exist. For schema migrations in production,
    # prefer `alembic upgrade head`; this create_all is idempotent and makes
    # a fresh deploy boot without a manual migration step.
    try:
        import app.models  # noqa: F401  (register all ORM models on Base)
        from app.database import init_db

        await init_db()
        logger.info("Database tables ensured")
    except Exception as e:
        logger.warning(f"Database init skipped/failed: {e}")

    # Start scheduler
    try:
        from app.scheduler.jobs import start_scheduler
        start_scheduler()
        logger.info("Scheduler started")
    except Exception as e:
        logger.warning(f"Scheduler failed to start: {e}")

    yield

    # Shutdown
    try:
        from app.scheduler.jobs import stop_scheduler
        stop_scheduler()
    except Exception:
        pass
    logger.info("BF1-2026 Quantitative System shut down")


app = FastAPI(
    title="BF1-2026 Quantitative System",
    version="1.0.0",
    description="Quantitative predictive analysis engine for BF1-2026",
    lifespan=lifespan,
)

# CORS
origins = settings.allowed_origins.split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
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
