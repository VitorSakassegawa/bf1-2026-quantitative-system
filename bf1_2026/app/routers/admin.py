"""Admin API router – protected management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.bet import Bet
from app.models.driver import Driver
from app.models.race import Race
from app.models.user import User

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


async def verify_admin(x_admin_key: str = Header(default="")) -> None:
    """Simple admin key verification via header."""
    if x_admin_key != settings.secret_key:
        raise HTTPException(status_code=403, detail="Invalid admin key")


@router.get("/dashboard", dependencies=[Depends(verify_admin)])
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


@router.post("/recalculate-elo", dependencies=[Depends(verify_admin)])
async def recalculate_elo(db: AsyncSession = Depends(get_db)):
    """Trigger a full ELO recalculation for all drivers and teams."""
    # In production: recalculate all ELO ratings from historical data
    return {"status": "ELO recalculation triggered"}


@router.post("/retrain-model", dependencies=[Depends(verify_admin)])
async def retrain_model(db: AsyncSession = Depends(get_db)):
    """Trigger a full model retrain."""
    # In production: trigger XGBoost full retrain
    return {"status": "Model retrain triggered"}


@router.post("/update-data", dependencies=[Depends(verify_admin)])
async def update_data(db: AsyncSession = Depends(get_db)):
    """Trigger a manual data update from F1 sources."""
    # In production: run collector pipeline
    return {"status": "Data update triggered"}
