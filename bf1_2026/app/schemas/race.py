"""Pydantic schemas for race-related endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class RaceBase(BaseModel):
    race_name: str
    round_number: int
    season: int
    is_sprint_weekend: bool = False


class RaceCreate(RaceBase):
    circuit_id: uuid.UUID
    race_date: datetime
    qualifying_date: datetime | None = None
    sprint_date: datetime | None = None


class RaceResponse(RaceBase):
    id: uuid.UUID
    circuit_id: uuid.UUID
    race_date: datetime
    qualifying_date: datetime | None
    sprint_date: datetime | None
    status: str
    deadline_bets: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class RaceAnalysis(BaseModel):
    race: RaceResponse
    weather_risk: float = Field(ge=0.0, le=1.0)
    rain_probability: float = Field(ge=0.0, le=1.0)
    temperature_air: float | None = None
    top_drivers: list[dict] = []
    recommended_strategy: dict = {}
    insights: list[str] = []
