"""Validation utilities for BF1-2026 business rules.

The rules themselves live in ``app.rules.pool_rules`` (mirroring the pool's
own published rules). The module-level constants here are kept as aliases so
existing imports continue to work, but they are derived, not re-declared.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.rules.pool_rules import DEFAULT_RULES, PoolRules, ResultLine

BF1_TOTAL_TOKENS = DEFAULT_RULES.total_chips
BF1_MAX_TOKENS_PER_DRIVER = DEFAULT_RULES.max_chips_per_driver
BF1_MIN_DRIVERS = DEFAULT_RULES.minimum_drivers
BF1_MIN_DIFFERENT_TEAMS = DEFAULT_RULES.minimum_drivers
# Flat, per driver — NOT per chip. See pool_rules for why that matters.
BF1_DNF_PENALTY = -int(DEFAULT_RULES.retirement_penalty)
BF1_SPRINT_MULTIPLIER = DEFAULT_RULES.multiplier(sprint=True)
BF1_ELEVENTH_BONUS = DEFAULT_RULES.eleventh_bonus

BF1_POINTS_TABLE = {
    pos: pts for pos, pts in enumerate(DEFAULT_RULES.position_points, start=1)
}


def validate_allocation(
    allocations: dict[str, int],
    team_membership: dict[str, str],
    rules: PoolRules | None = None,
) -> list[str]:
    """
    Validate a token allocation against all BF1-2026 rules.
    Returns a list of error messages (empty if valid).
    """
    return (rules or DEFAULT_RULES).validate(allocations, team_membership)


def bet_deadline_for(race_date: datetime, deadline_bets: datetime | None = None) -> datetime:
    """The instant betting closes.

    The pool accepts bets "até o horário oficial da prova", i.e. until the
    scheduled start — not an hour before, which is what this used to assume.
    A stored deadline (synced from the pool's own calendar) always wins.
    """
    if deadline_bets is not None:
        return _as_utc(deadline_bets)
    return _as_utc(race_date)


def is_bet_deadline_passed(
    race_date: datetime,
    deadline_bets: datetime | None = None,
) -> bool:
    """Check whether betting has closed for a race.

    Pass the race's stored `deadline_bets` whenever it is available. Deriving
    the deadline from `race_date` alone is only safe if `race_date` is the real
    session start: seeded races used to carry a placeholder 14:00 UTC, so for
    an early race (Melbourne starts 04:00 UTC) the derived deadline landed
    hours *after* the chequered flag.

    Naive datetimes are treated as UTC rather than compared against an aware
    `now`, which would raise TypeError.
    """
    now = datetime.now(timezone.utc)
    return now >= bet_deadline_for(race_date, deadline_bets)


def _as_utc(dt: datetime) -> datetime:
    """Normalise a possibly-naive datetime to an aware UTC one."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def calculate_bf1_points(
    position: int | None,
    dnf: bool,
    chips: int = 1,
    sprint: bool = False,
    rules: PoolRules | None = None,
) -> float:
    """Points one driver contributes to a bet: chips x points, or a flat penalty.

    The old signature took a ``fastest_lap`` flag and added a bonus point. The
    pool has no fastest-lap bonus (and F1 itself dropped the point in 2025).
    """
    rules = rules or DEFAULT_RULES
    line = ResultLine(position=None if dnf else position, retired=dnf)
    return rules.score({"d": chips}, None, {"d": line}, sprint=sprint).total
