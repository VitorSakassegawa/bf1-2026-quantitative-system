"""Pydantic schemas for driver-related endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel


class DriverBase(BaseModel):
    name: str
    code: str
    number: int
    nationality: str


class DriverCreate(DriverBase):
    team_id: uuid.UUID


class DriverResponse(DriverBase):
    id: uuid.UUID
    team_id: uuid.UUID
    current_elo: float
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class DriverKPIs(BaseModel):
    driver_id: uuid.UUID
    circuit_id: uuid.UUID | None = None
    avg_finish_position: float | None = None
    avg_grid_position: float | None = None
    avg_positions_gained: float | None = None
    performance_std: float | None = None
    dnf_rate: float | None = None
    wet_performance: float | None = None
    sprint_performance: float | None = None
    consistency_index: float | None = None
    momentum: float | None = None
    volatility: float | None = None
