"""Backtest the engine against real results, scored with the pool's rules.

    python -m scripts.backtest                 # current season
    python -m scripts.backtest 2026            # one season
    python -m scripts.backtest 2023 2026       # inclusive range
    python -m scripts.backtest 2026 --events   # per-event detail

History from the start of the regulation era is always replayed first so form
and ratings are warm; only the requested seasons are bet on. Downloads are
cached under BACKTEST_CACHE (default ./.backtest_cache) — finished seasons
never change, the current one is refreshed on every run.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from app.collectors.f1_api_fallback import F1ApiFallback
from app.models_ml.backtest import BacktestReport, Event, build_events, walk_forward
from app.rules.pool_rules import DEFAULT_RULES
from app.utils.season_window import era_start_for

CACHE = Path(os.getenv("BACKTEST_CACHE", ".backtest_cache"))


def _dump(obj):
    return obj.isoformat() if isinstance(obj, datetime) else str(obj)


def _revive(calendar: list[dict]) -> list[dict]:
    for c in calendar:
        for key in ("start", "sprint_start", "qualifying_start"):
            if isinstance(c.get(key), str):
                c[key] = datetime.fromisoformat(c[key])
    return calendar


async def load_season(collector: F1ApiFallback, season: int, refresh: bool) -> list[Event]:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{season}.json"
    if path.exists() and not refresh:
        data = json.loads(path.read_text())
    else:
        calendar = await collector.fetch_calendar(season)
        results = await collector.fetch_season_results(season)
        sprints = await collector.fetch_season_sprints(season)
        data = {"calendar": calendar, "results": results, "sprints": sprints}
        path.write_text(json.dumps(data, default=_dump))
        data = json.loads(path.read_text())
    return build_events(
        _revive(data["calendar"]),
        {int(k): v for k, v in data["results"].items()},
        {int(k): v for k, v in data["sprints"].items()},
        season,
    )


def _line(label: str, rep: BacktestReport, season: int | None) -> str:
    n = rep.events(season) or 1
    return (
        f"  {label:28} total {rep.total(season):8.1f}   avg {rep.total(season) / n:6.1f}"
        f"   chips {rep.chip_total(season):8.1f}   11th hits {rep.hits(season):2d}/{rep.events(season)}"
    )


async def run(start: int, end: int, show_events: bool) -> None:
    now = datetime.now(timezone.utc).year
    first = min(era_start_for(start), start)
    collector = F1ApiFallback()
    events: list[Event] = []
    try:
        for season in range(first, end + 1):
            events.extend(await load_season(collector, season, refresh=season >= now))
    finally:
        await collector.close()

    seasons = set(range(start, end + 1))
    model = walk_forward(events, seasons, strategy="model")
    grid = walk_forward(events, seasons, strategy="grid")

    print()
    print("=== BF1 backtest — recommended bets scored with the pool's rules ===")
    for season in sorted(seasons):
        if not model.events(season):
            continue
        races = sum(1 for o in model.outcomes if o.event.season == season and not o.event.sprint)
        sprints = model.events(season) - races
        ceiling = races * DEFAULT_RULES.max_score(False) + sprints * DEFAULT_RULES.max_score(True)
        print(f"\n{season}: {races} races, {sprints} sprints  (pool maximum {ceiling:.0f})")
        print(_line("engine", model, season))
        print(_line("baseline: bet the grid", grid, season))
        print(
            f"  calibration: points RMSE {model.rmse(season):.3f}   "
            f"DNF Brier {model.brier(season):.4f}   "
            f"log-lik of actual 11th {model.ll11(season):.3f} "
            f"(a coin-flip among 21 cars is -3.04)"
        )

    if len(seasons) > 1:
        print("\nALL SEASONS")
        print(_line("engine", model, None))
        print(_line("baseline: bet the grid", grid, None))

    if show_events:
        print("\nPER EVENT (engine)")
        for o in model.outcomes:
            codes = {r.driver_id: r.code or r.driver_id[:3].upper() for r in o.event.rows}
            bet = " ".join(f"{codes[d]}{c}" for d, c in o.allocation.items())
            pick = codes.get(o.eleventh_pick, "-") if o.eleventh_pick else "-"
            print(
                f"  {o.event.label[:38]:38} {bet:28} 11th={pick}{'*' if o.eleventh_hit else ' '}"
                f" score {o.score:6.1f}  expected {o.expected_score:6.1f}"
            )
    print()
    print(
        "Read this with care: one season is ~26 events and a single bet swings by\n"
        "about +/-60 points, so engine-vs-grid gaps of a few hundred points are noise.\n"
        "The calibration line is the reliable signal for whether to retune."
    )


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    year = datetime.now(timezone.utc).year
    s = int(args[0]) if args else year
    e = int(args[1]) if len(args) > 1 else s
    asyncio.run(run(s, e, "--events" in sys.argv))
