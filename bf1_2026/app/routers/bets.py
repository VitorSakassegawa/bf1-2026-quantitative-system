"""Bets API router."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.bet import Bet, BetStatus
from app.models.race import Race
from app.schemas.bet import BetCreate, BetResponse
from app.utils.validators import is_bet_deadline_passed

router = APIRouter(prefix="/api/v1/bets", tags=["bets"])


@router.post("/", response_model=BetResponse, status_code=201)
async def create_bet(
    bet_in: BetCreate,
    telegram_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Register a new bet (token allocation)."""
    # Check race exists and deadline hasn't passed
    result = await db.execute(select(Race).where(Race.id == bet_in.race_id))
    race = result.scalar_one_or_none()
    if race is None:
        raise HTTPException(status_code=404, detail="Race not found")

    if is_bet_deadline_passed(race.race_date, race.deadline_bets):
        raise HTTPException(
            status_code=400,
            detail="Betting deadline has passed (1h before race)",
        )

    # In production: look up user by telegram_id, validate team diversity
    bet = Bet(
        user_id=uuid.uuid4(),  # placeholder
        race_id=bet_in.race_id,
        strategy_type=bet_in.strategy_type,
        allocations=bet_in.allocations,
        confirmed_at=datetime.now(timezone.utc),
        status=BetStatus.confirmed,
    )
    db.add(bet)
    await db.flush()
    await db.refresh(bet)
    return bet


@router.get("/user/{telegram_id}", response_model=list[BetResponse])
async def get_user_bets(
    telegram_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get all bets for a user by Telegram ID."""
    from app.models.user import User

    user_result = await db.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    result = await db.execute(
        select(Bet)
        .where(Bet.user_id == user.id)
        .order_by(Bet.created_at.desc())
    )
    return result.scalars().all()


@router.get("/leaderboard")
async def get_leaderboard(
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
):
    """Get the top users by total BF1 points."""
    from app.models.user import User

    result = await db.execute(
        select(User)
        .order_by(User.total_points.desc())
        .limit(limit)
    )
    users = result.scalars().all()

    return [
        {
            "rank": i + 1,
            "username": u.username or f"User {u.telegram_id}",
            "total_points": u.total_points,
            "total_bets": u.total_bets,
        }
        for i, u in enumerate(users)
    ]
