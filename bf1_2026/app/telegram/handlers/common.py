"""Shared plumbing for the bot's strategy commands."""

from __future__ import annotations

import uuid

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.pool_client import PoolError
from app.models.race import Race
from app.rules.pool_rules import DEFAULT_RULES
from app.services import pool_service
from app.services.grid_service import maybe_refresh_grid
from app.services.strategy_service import build_strategy, get_next_or_latest_race

BOT_SIMULATIONS = 20000

NO_DATA_MESSAGE = (
    "Ainda não há corridas no banco. Rode o pipeline primeiro:\n"
    "docker exec bf1_api python -m scripts.pipeline"
)


async def resolve_race(session: AsyncSession, args: list[str]) -> Race | None:
    """The race named by an optional id argument, else the next one."""
    if args:
        try:
            race = (
                await session.execute(select(Race).where(Race.id == uuid.UUID(args[0])))
            ).scalar_one_or_none()
            if race is not None:
                return race
        except (ValueError, TypeError):
            pass
    return await get_next_or_latest_race(session)


async def pool_context() -> dict | None:
    """What the pool says about the event open for betting, if it can be asked."""
    if not pool_service.pool_enabled():
        return None
    try:
        return await pool_service.fetch_next_event()
    except PoolError as e:
        logger.warning(f"Pool context unavailable: {e}")
        return None


async def strategy_for(
    session: AsyncSession, race: Race, aggressiveness: str = "balanced"
) -> tuple[dict, dict | None]:
    """Build the recommended bet for a race, using everything available.

    Pulls the official grid if it has been published and is not stored yet,
    and takes the rules, the deadline and the session type (race or sprint)
    from the pool when it is configured.
    """
    await maybe_refresh_grid(session, race)
    pool = await pool_context()
    rules = pool["rules"] if pool else DEFAULT_RULES
    sprint = bool(pool and pool.get("type") == "Sprint" and race.is_sprint_weekend)
    strategy = await build_strategy(
        session, race, aggressiveness=aggressiveness,
        n_simulations=BOT_SIMULATIONS, sprint=sprint, rules=rules,
    )
    return strategy, pool
