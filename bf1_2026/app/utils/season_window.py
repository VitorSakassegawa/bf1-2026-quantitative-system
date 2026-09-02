"""Regulation eras and the historical window used by ELO and the KPIs.

Two related problems this module exists to solve.

**The window was never applied.** Both `CircuitKPICalculator` and
`DriverKPICalculator` accept `window_years=5`, store it, and never reference it
again — every caller passed the whole result history, so a 2021 race weighed
exactly as much as a 2026 one.

**Regulation resets break comparability.** F1 rewrites its technical rules
periodically, and the competitive order resets with them. Pooling across a
reset does not just add noise, it adds bias: it carries the previous era's
pecking order into a season where it no longer holds. 2026 is the sharpest
example in the sport's recent history — new chassis and power-unit rules, an
eleventh team — and the standings bear that out.

So: prefer data from the current era, fall back to the recency window only when
the current era is too thin to say anything, and regress ELO toward the
baseline when crossing an era boundary rather than carrying ratings straight
across.
"""

from __future__ import annotations

from datetime import datetime, timezone

# First season of each modern regulation era, most recent last.
#   2014 - V6 hybrid turbo power units
#   2017 - wider cars, larger aero
#   2022 - ground-effect floors, 18-inch wheels
#   2026 - new chassis and power-unit rules; Cadillac joins, Audi takes over
#          the Sauber entry
REGULATION_ERA_STARTS: tuple[int, ...] = (2014, 2017, 2022, 2026)

DEFAULT_WINDOW_YEARS = 5

# Below this many rows an era-only sample says more about sampling noise than
# about the circuit or driver, so widen to the recency window instead.
MIN_ROWS_FOR_ERA_ONLY = 8

# How far ELO regresses toward the baseline when crossing an era boundary.
# 0.0 carries ratings straight across (the old behaviour); 1.0 wipes them.
# 0.5 keeps half the accumulated signal — driver skill does carry over, the
# machinery order does not.
ERA_ELO_REGRESSION = 0.5


def current_season(now: datetime | None = None) -> int:
    """The season currently being contested.

    A season runs to early December, so anything before then belongs to the
    calendar year; the short off-season is attributed to the year just ended.
    """
    now = now or datetime.now(timezone.utc)
    return now.year


def era_start_for(season: int) -> int:
    """First season of the regulation era `season` belongs to."""
    start = REGULATION_ERA_STARTS[0]
    for boundary in REGULATION_ERA_STARTS:
        if season >= boundary:
            start = boundary
        else:
            break
    return start


def same_era(season_a: int, season_b: int) -> bool:
    return era_start_for(season_a) == era_start_for(season_b)


def crosses_era_boundary(previous_season: int, next_season: int) -> bool:
    """True when moving from one season to the next changes regulation era."""
    return era_start_for(previous_season) != era_start_for(next_season)


def regress_elo_for_new_era(
    elo: float,
    baseline: float,
    regression: float = ERA_ELO_REGRESSION,
) -> float:
    """Pull a rating toward the baseline at an era boundary.

    Standard practice when the underlying competition resets: keep part of the
    accumulated signal rather than all of it or none. Without this, five
    seasons of pre-reset history dominate a reset season — the engine would
    carry a Verstappen/McLaren prior into a Mercedes-dominant 2026.
    """
    regression = min(max(float(regression), 0.0), 1.0)
    return elo + (baseline - elo) * regression


def window_start_season(
    window_years: int = DEFAULT_WINDOW_YEARS,
    season: int | None = None,
) -> int:
    """Earliest season inside the recency window, inclusive."""
    season = season if season is not None else current_season()
    return season - max(int(window_years), 1) + 1


def seasons_in_scope(
    available_seasons: list[int],
    window_years: int = DEFAULT_WINDOW_YEARS,
    season: int | None = None,
    min_rows: int = 0,
    rows_per_season: dict[int, int] | None = None,
) -> list[int]:
    """Pick the seasons a KPI should be computed from.

    Prefers the current regulation era. Falls back to the recency window when
    the era holds fewer than `min_rows` rows, and finally to everything
    available so a brand-new era still produces a number rather than a null.
    """
    if not available_seasons:
        return []

    season = season if season is not None else current_season()
    era_start = era_start_for(season)

    era_seasons = sorted(s for s in available_seasons if s >= era_start)
    if era_seasons and rows_per_season is not None:
        rows = sum(rows_per_season.get(s, 0) for s in era_seasons)
        if rows >= max(min_rows, 1):
            return era_seasons
    elif era_seasons and rows_per_season is None:
        return era_seasons

    window_start = window_start_season(window_years, season)
    windowed = sorted(s for s in available_seasons if s >= window_start)
    return windowed or sorted(available_seasons)
