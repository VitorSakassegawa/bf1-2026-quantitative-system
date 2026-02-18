"""Pydantic schemas for bet-related endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class BetCreate(BaseModel):
    race_id: uuid.UUID
    strategy_type: str = "balanced"
    allocations: dict[str, int]

    @field_validator("allocations")
    @classmethod
    def validate_allocations(cls, v: dict[str, int]) -> dict[str, int]:
        total = sum(v.values())
        if total != 15:
            raise ValueError(f"Total tokens must be 15, got {total}")
        if any(tokens < 1 or tokens > 5 for tokens in v.values()):
            raise ValueError("Each driver must have 1-5 tokens")
        if len(v) < 5:
            raise ValueError("Must select at least 5 drivers")
        return v


class BetResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    race_id: uuid.UUID
    strategy_type: str
    total_tokens: int
    allocations: dict
    expected_value: float
    confirmed_at: datetime | None
    actual_points: float | None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AllocationSuggestion(BaseModel):
    driver_id: str
    driver_name: str
    driver_code: str
    tokens: int = Field(ge=1, le=5)
    expected_value: float
    top3_probability: float
    dnf_probability: float
    reasoning: str
