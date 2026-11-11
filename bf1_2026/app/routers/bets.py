"""Bets API router."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.bet import Bet, BetStatus, StrategyType
from app.models.driver import Driver
from app.models.race import Race
from app.models.user import User
from app.schemas.bet import BetCreate, BetResponse
from app.utils.security import require_write_key
from app.utils.validators import is_bet_deadline_passed, validate_allocation

router = APIRouter(prefix="/api/v1/bets", tags=["bets"])


@router.post(
    "/",
    response_model=BetResponse,
    status_code=201,
    dependencies=[Depends(require_write_key)],
)
async def create_bet(
    bet_in: BetCreate,
    telegram_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Register a new bet (token allocation) for a Telegram user.

    `telegram_id` is required: the bet has to belong to a real user row. This
    previously generated a random UUID as a placeholder, which could only ever
    raise a foreign-key violation, so the endpoint had never succeeded.
    """
    user = (
        await db.execute(select(User).where(User.telegram_id == telegram_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=404, detail=f"No user registered for telegram_id {telegram_id}"
        )

    race = (
        await db.execute(select(Race).where(Race.id == bet_in.race_id))
    ).scalar_one_or_none()
    if race is None:
        raise HTTPException(status_code=404, detail="Race not found")

    if is_bet_deadline_passed(race.race_date, race.deadline_bets):
        raise HTTPException(
            status_code=400,
            detail="Betting deadline has passed (bets close at the race start)",
        )

    # Enforce the BF1 rules here too. The optimizer produces valid allocations,
    # but this endpoint accepts hand-built ones.
    allocations = bet_in.allocations or {}
    driver_ids = list(allocations.keys())
    rows = (
        await db.execute(
            select(Driver.id, Driver.team_id).where(Driver.id.in_(driver_ids))
        )
    ).all()
    if len(rows) != len(driver_ids):
        raise HTTPException(
            status_code=422, detail="Allocation references unknown driver ids"
        )

    team_membership = {str(did): str(tid) for did, tid in rows}
    errors = validate_allocation(
        {str(k): v for k, v in allocations.items()}, team_membership
    )
    if errors:
        raise HTTPException(status_code=422, detail={"constraint_errors": errors})

    if bet_in.eleventh_driver_id is not None:
        known = await db.scalar(select(Driver.id).where(Driver.id == bet_in.eleventh_driver_id))
        if known is None:
            raise HTTPException(status_code=422, detail="Unknown eleventh_driver_id")

    # As on the pool, a new bet for the same race replaces the earlier one.
    bet = (
        await db.execute(select(Bet).where(Bet.user_id == user.id, Bet.race_id == race.id))
    ).scalar_one_or_none()
    if bet is None:
        bet = Bet(user_id=user.id, race_id=bet_in.race_id, allocations={})
        user.total_bets += 1
    bet.strategy_type = StrategyType(bet_in.strategy_type)
    bet.allocations = allocations
    bet.eleventh_driver_id = bet_in.eleventh_driver_id
    bet.confirmed_at = datetime.now(timezone.utc)
    bet.status = BetStatus.confirmed
    bet.actual_points = None
    bet.eleventh_hit = None
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
