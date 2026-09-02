"""Validation utilities for BF1-2026 business rules."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone


BF1_TOTAL_TOKENS = 15
BF1_MAX_TOKENS_PER_DRIVER = 5
BF1_MIN_DRIVERS = 5
BF1_MIN_DIFFERENT_TEAMS = 5
BF1_DNF_PENALTY = -10
BF1_SPRINT_MULTIPLIER = 2.0
BF1_FASTEST_LAP_BONUS = 1

BF1_POINTS_TABLE = {
    1: 25, 2: 18, 3: 15, 4: 12, 5: 10,
    6: 8, 7: 6, 8: 4, 9: 2, 10: 1,
}


def validate_allocation(
    allocations: dict[str, int],
    team_membership: dict[str, str],
) -> list[str]:
    """
    Validate a token allocation against all BF1-2026 rules.
    Returns a list of error messages (empty if valid).
    """
    errors: list[str] = []

    total_tokens = sum(allocations.values())
    if total_tokens != BF1_TOTAL_TOKENS:
        errors.append(f"Total tokens must be {BF1_TOTAL_TOKENS}, got {total_tokens}")

    for driver_id, tokens in allocations.items():
        if tokens < 1:
            errors.append(f"Driver {driver_id}: minimum 1 token, got {tokens}")
        if tokens > BF1_MAX_TOKENS_PER_DRIVER:
            errors.append(
                f"Driver {driver_id}: maximum {BF1_MAX_TOKENS_PER_DRIVER} tokens, got {tokens}"
            )

    if len(allocations) < BF1_MIN_DRIVERS:
        errors.append(
            f"Must select at least {BF1_MIN_DRIVERS} drivers, got {len(allocations)}"
        )

    teams_used = set()
    for driver_id in allocations:
        team = team_membership.get(driver_id)
        if team:
            teams_used.add(team)
    if len(teams_used) < BF1_MIN_DIFFERENT_TEAMS:
        errors.append(
            f"Must use at least {BF1_MIN_DIFFERENT_TEAMS} different teams, got {len(teams_used)}"
        )

    return errors


def bet_deadline_for(race_date: datetime, deadline_bets: datetime | None = None) -> datetime:
    """The instant betting closes: the stored deadline, else 1h before lights out."""
    if deadline_bets is not None:
        return _as_utc(deadline_bets)
    return _as_utc(race_date) - timedelta(hours=1)


def is_bet_deadline_passed(
    race_date: datetime,
    deadline_bets: datetime | None = None,
) -> bool:
    """Check whether betting has closed for a race.

    Pass the race's stored `deadline_bets` whenever it is available. Deriving
    the deadline from `race_date` alone is only safe if `race_date` is the real
    session start: seeded races used to carry a placeholder 14:00 UTC, so for
    an early race (Melbourne starts 04:00 UTC) the derived deadline landed
    ~9 hours *after* the chequered flag and bets could still be placed on a
    race whose result was already public.

    Naive datetimes are treated as UTC rather than compared against an aware
    `now`, which would raise TypeError.
    """
    now = datetime.now(timezone.utc)
    return now >= bet_deadline_for(race_date, deadline_bets)


def _as_utc(dt: datetime) -> datetime:
    """Normalise a possibly-naive datetime to an aware UTC one."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def calculate_bf1_points(position: int | None, dnf: bool, fastest_lap: bool) -> float:
    """Calculate BF1 points for a given result."""
    if dnf:
        return float(BF1_DNF_PENALTY)
    if position is None:
        return 0.0
    points = float(BF1_POINTS_TABLE.get(position, 0))
    if fastest_lap and position is not None and position <= 10:
        points += BF1_FASTEST_LAP_BONUS
    return points
