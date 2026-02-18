"""Races API router."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.race import Race
from app.schemas.race import RaceAnalysis, RaceCreate, RaceResponse

router = APIRouter(prefix="/api/v1/races", tags=["races"])


@router.get("/", response_model=list[RaceResponse])
async def list_races(
    season: int = 2026,
    db: AsyncSession = Depends(get_db),
):
    """List all races for a season."""
    result = await db.execute(
        select(Race).where(Race.season == season).order_by(Race.round_number)
    )
    races = result.scalars().all()
    return races


@router.get("/{race_id}", response_model=RaceResponse)
async def get_race(
    race_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get race details by ID."""
    result = await db.execute(select(Race).where(Race.id == race_id))
    race = result.scalar_one_or_none()
    if race is None:
        raise HTTPException(status_code=404, detail="Race not found")
    return race


@router.post("/", response_model=RaceResponse, status_code=201)
async def create_race(
    race_in: RaceCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new race."""
    from datetime import timedelta

    race = Race(
        circuit_id=race_in.circuit_id,
        season=race_in.season,
        round_number=race_in.round_number,
        race_name=race_in.race_name,
        race_date=race_in.race_date,
        qualifying_date=race_in.qualifying_date,
        sprint_date=race_in.sprint_date,
        is_sprint_weekend=race_in.is_sprint_weekend,
        deadline_bets=race_in.race_date - timedelta(hours=1),
    )
    db.add(race)
    await db.flush()
    await db.refresh(race)
    return race


@router.get("/{race_id}/analysis")
async def get_race_analysis(
    race_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get complete analysis for a race."""
    result = await db.execute(select(Race).where(Race.id == race_id))
    race = result.scalar_one_or_none()
    if race is None:
        raise HTTPException(status_code=404, detail="Race not found")

    # Placeholder – in production, call StrategyEngine
    return {
        "race_id": str(race.id),
        "race_name": race.race_name,
        "status": race.status.value,
        "analysis": "Run /api/v1/races/{race_id}/strategy for full analysis",
    }


@router.get("/{race_id}/strategy")
async def get_race_strategy(
    race_id: uuid.UUID,
    aggressiveness: str = "balanced",
    db: AsyncSession = Depends(get_db),
):
    """Get optimal strategy for a race."""
    result = await db.execute(select(Race).where(Race.id == race_id))
    race = result.scalar_one_or_none()
    if race is None:
        raise HTTPException(status_code=404, detail="Race not found")

    # Placeholder – in production, call StrategyEngine.generate_strategy()
    return {
        "race_id": str(race.id),
        "race_name": race.race_name,
        "aggressiveness": aggressiveness,
        "strategy": "Strategy generation requires data collection first",
    }
