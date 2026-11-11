"""Admin API router – protected management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.bet import Bet
from app.models.driver import Driver
from app.models.race import Race
from app.models.user import User
from app.utils.security import require_admin_key

# Guard the whole router rather than each route, so a new admin endpoint is
# protected by default instead of by remembering to add the dependency.
router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_key)],
)

# Kept as an alias so existing imports of `verify_admin` keep working.
verify_admin = require_admin_key


@router.get("/dashboard")
async def admin_dashboard(db: AsyncSession = Depends(get_db)):
    """Admin dashboard – system overview."""
    users_count = await db.scalar(select(func.count()).select_from(User))
    races_count = await db.scalar(select(func.count()).select_from(Race))
    drivers_count = await db.scalar(
        select(func.count()).select_from(Driver).where(Driver.is_active == True)  # noqa: E712
    )
    bets_count = await db.scalar(select(func.count()).select_from(Bet))

    return {
        "total_users": users_count or 0,
        "total_races": races_count or 0,
        "active_drivers": drivers_count or 0,
        "total_bets": bets_count or 0,
        "environment": settings.environment.value,
    }


@router.post("/update-data")
async def update_data():
    """Sync the current season from the results API and rebuild everything
    derived from it (ratings, KPIs, bet settlement, pool deadlines)."""
    from app.scheduler.jobs import run_data_update

    return {"status": "ok", **(await run_data_update())}


@router.post("/recalculate-elo")
async def recalculate_elo():
    """Replay every stored race to recompute ratings and KPIs."""
    from scripts.build_intelligence import build

    await build()
    return {"status": "ok"}


@router.post("/refresh-grid")
async def refresh_grid(db: AsyncSession = Depends(get_db)):
    """Fetch the next race's official starting grid from formula1.com now."""
    from app.services.grid_service import refresh_grid as do_refresh
    from app.services.strategy_service import get_next_or_latest_race

    race = await get_next_or_latest_race(db)
    if race is None:
        return {"status": "no race"}
    stored = await do_refresh(db, race)
    return {"status": "ok", "race": race.race_name, "stored": stored}


@router.post("/settle-bets")
async def settle(db: AsyncSession = Depends(get_db)):
    """Score every recorded bet whose race has results."""
    from app.services.settlement_service import settle_bets

    return {"status": "ok", "settled": await settle_bets(db)}
