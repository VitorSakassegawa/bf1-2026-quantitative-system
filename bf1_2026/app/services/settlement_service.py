"""Score recorded bets against real results, with the pool's rules.

Until this existed nothing ever wrote ``Bet.actual_points`` or
``User.total_points``: bets could be recorded but never settled, so the local
leaderboard showed zero for everyone, permanently.
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bet import Bet, BetStatus
from app.models.race_result import RaceResult
from app.models.user import User
from app.rules.pool_rules import DEFAULT_RULES, PoolRules, ResultLine

# A car that never started is not classified but is not charged the penalty.
NOT_STARTED = {"Did not start", "Withdrew", "Did not qualify", "Did not prequalify", "DNS"}


def result_lines(rows: list[RaceResult]) -> dict[str, ResultLine]:
    """Turn stored race results into what the scorer needs."""
    lines: dict[str, ResultLine] = {}
    for r in rows:
        classified = not r.dnf and r.final_position is not None
        retired = r.dnf and (r.dnf_reason or "") not in NOT_STARTED
        lines[str(r.driver_id)] = ResultLine(
            position=r.final_position if classified else None, retired=bool(retired)
        )
    return lines


async def settle_bets(session: AsyncSession, rules: PoolRules = DEFAULT_RULES) -> int:
    """Score every unsettled bet whose race has results. Returns how many."""
    open_bets = (
        await session.execute(
            select(Bet).where(Bet.status.in_([BetStatus.confirmed, BetStatus.pending]))
        )
    ).scalars().all()
    if not open_bets:
        return 0

    settled = 0
    cache: dict = {}
    for bet in open_bets:
        if bet.race_id not in cache:
            rows = (
                await session.execute(
                    select(RaceResult).where(RaceResult.race_id == bet.race_id)
                )
            ).scalars().all()
            cache[bet.race_id] = result_lines(list(rows)) if rows else None
        lines = cache[bet.race_id]
        if not lines:
            continue        # race not run yet
        breakdown = rules.score(
            {str(k): int(v) for k, v in (bet.allocations or {}).items()},
            str(bet.eleventh_driver_id) if bet.eleventh_driver_id else None,
            lines,
        )
        bet.actual_points = breakdown.total
        bet.eleventh_hit = breakdown.eleventh_hit if bet.eleventh_driver_id else None
        bet.status = BetStatus.scored
        settled += 1

    if settled:
        await session.flush()
        totals = dict(
            (
                await session.execute(
                    select(Bet.user_id, func.coalesce(func.sum(Bet.actual_points), 0.0))
                    .where(Bet.status == BetStatus.scored)
                    .group_by(Bet.user_id)
                )
            ).all()
        )
        counts = dict(
            (await session.execute(select(Bet.user_id, func.count()).group_by(Bet.user_id))).all()
        )
        for user in (await session.execute(select(User))).scalars().all():
            user.total_points = float(totals.get(user.id, 0.0))
            user.total_bets = int(counts.get(user.id, 0))
        logger.info(f"Settled {settled} bet(s)")
    return settled
