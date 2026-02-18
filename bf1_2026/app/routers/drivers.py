"""Drivers API router."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.driver import Driver
from app.schemas.driver import DriverCreate, DriverKPIs, DriverResponse

router = APIRouter(prefix="/api/v1/drivers", tags=["drivers"])


@router.get("/", response_model=list[DriverResponse])
async def list_drivers(
    active_only: bool = True,
    db: AsyncSession = Depends(get_db),
):
    """List all drivers (optionally only active ones)."""
    query = select(Driver)
    if active_only:
        query = query.where(Driver.is_active == True)  # noqa: E712
    query = query.order_by(Driver.current_elo.desc())
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{driver_id}", response_model=DriverResponse)
async def get_driver(
    driver_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get driver details by ID."""
    result = await db.execute(select(Driver).where(Driver.id == driver_id))
    driver = result.scalar_one_or_none()
    if driver is None:
        raise HTTPException(status_code=404, detail="Driver not found")
    return driver


@router.post("/", response_model=DriverResponse, status_code=201)
async def create_driver(
    driver_in: DriverCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new driver."""
    driver = Driver(
        name=driver_in.name,
        code=driver_in.code,
        number=driver_in.number,
        nationality=driver_in.nationality,
        team_id=driver_in.team_id,
    )
    db.add(driver)
    await db.flush()
    await db.refresh(driver)
    return driver


@router.get("/{driver_id}/kpis", response_model=DriverKPIs)
async def get_driver_kpis(
    driver_id: uuid.UUID,
    circuit_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Get computed KPIs for a driver, optionally at a specific circuit."""
    result = await db.execute(select(Driver).where(Driver.id == driver_id))
    driver = result.scalar_one_or_none()
    if driver is None:
        raise HTTPException(status_code=404, detail="Driver not found")

    # Placeholder – in production, call DriverKPICalculator
    return DriverKPIs(
        driver_id=driver.id,
        circuit_id=circuit_id,
        avg_finish_position=None,
        avg_grid_position=None,
        avg_positions_gained=None,
        performance_std=None,
        dnf_rate=None,
        wet_performance=None,
        sprint_performance=None,
        consistency_index=None,
        momentum=None,
        volatility=None,
    )
