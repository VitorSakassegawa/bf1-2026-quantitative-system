"""Pydantic schemas for prediction-related endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class PredictionResponse(BaseModel):
    id: uuid.UUID
    race_id: uuid.UUID
    driver_id: uuid.UUID
    top3_probability: float = Field(ge=0.0, le=1.0)
    top10_probability: float = Field(ge=0.0, le=1.0)
    dnf_probability: float = Field(ge=0.0, le=1.0)
    expected_points: float
    expected_value: float
    recommended_tokens: int
    model_version: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    created_at: datetime

    model_config = {"from_attributes": True}


class SimulationRequest(BaseModel):
    n_simulations: int = Field(default=20000, ge=1000, le=100000)


class SimulationResponse(BaseModel):
    race_id: uuid.UUID
    n_simulations: int
    model_version: str
    driver_results: dict
    run_at: datetime
