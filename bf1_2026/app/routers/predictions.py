"""Predictions API router."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.prediction import Prediction
from app.models.race import Race
from app.schemas.prediction import (
    PredictionResponse,
    SimulationRequest,
    SimulationResponse,
)

router = APIRouter(prefix="/api/v1/predictions", tags=["predictions"])


@router.get("/race/{race_id}", response_model=list[PredictionResponse])
async def get_predictions(
    race_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get all predictions for a race."""
    result = await db.execute(
        select(Prediction)
        .where(Prediction.race_id == race_id)
        .order_by(Prediction.expected_value.desc())
    )
    predictions = result.scalars().all()
    if not predictions:
        raise HTTPException(
            status_code=404, detail="No predictions found for this race"
        )
    return predictions


@router.post("/race/{race_id}/simulate", response_model=SimulationResponse)
async def run_simulation(
    race_id: uuid.UUID,
    sim_req: SimulationRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Run Monte Carlo simulation for a race."""
    result = await db.execute(select(Race).where(Race.id == race_id))
    race = result.scalar_one_or_none()
    if race is None:
        raise HTTPException(status_code=404, detail="Race not found")

    n_sims = sim_req.n_simulations if sim_req else 20000

    # Placeholder – in production, call MonteCarloSimulator
    return SimulationResponse(
        race_id=race.id,
        n_simulations=n_sims,
        model_version="placeholder",
        driver_results={},
        run_at=datetime.now(timezone.utc),
    )
