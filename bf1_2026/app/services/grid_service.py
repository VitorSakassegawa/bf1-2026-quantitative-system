"""Fetch and store the official starting grid for a race.

The results API only knows a race's grid once the race is over. Before the
start — when the bet is placed — the official grid, penalties applied, is
published on formula1.com. This pulls it, stores it on the race, and brings
the active roster in line with who is actually on it (a stand-in driver
appears here first).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.f1_scraper import F1DataCollector
from app.models.driver import Driver
from app.models.race import Race, RaceStatus
from app.models.team import Team

MIN_GRID_SIZE = 10      # anything smaller is not a believable grid


def _match_team(site_name: str, teams: list[Team]) -> Team | None:
    """Best local team for a formula1.com team name ("Red Bull Racing")."""
    site = site_name.lower().strip()
    best, best_len = None, 0
    for team in teams:
        for candidate in (team.name, team.full_name):
            c = (candidate or "").lower().strip()
            if c and (c == site or c in site or site in c) and len(c) > best_len:
                best, best_len = team, len(c)
    return best


async def store_grid(
    session: AsyncSession, race: Race, rows: list[dict], sprint: bool = False
) -> bool:
    """Persist a scraped grid on the race. Returns True when it was stored."""
    if len(rows) < MIN_GRID_SIZE:
        return False

    drivers = {d.code: d for d in (await session.execute(select(Driver))).scalars().all()}
    teams = list((await session.execute(select(Team))).scalars().all())
    active_teams = [t for t in teams if t.is_active] or teams

    grid: dict[str, int] = {}
    on_grid: set = set()
    for row in rows:
        code = row.get("driver_code", "")
        team = _match_team(row.get("team", ""), active_teams)
        driver = drivers.get(code)
        if driver is None:
            if team is None:
                logger.warning(f"Grid driver {code} has an unknown team; skipped")
                continue
            driver = Driver(
                name=row.get("driver") or code,
                code=code,
                number=row.get("number") or 0,
                nationality="Unknown",
                team_id=team.id,
                is_active=True,
            )
            session.add(driver)
            await session.flush()
            drivers[code] = driver
            logger.info(f"New driver on the grid: {code} ({team.name})")
        elif team is not None and driver.team_id != team.id:
            logger.info(f"{code} is driving for {team.name} this weekend")
            driver.team_id = team.id
        grid[str(driver.id)] = int(row["position"])
        on_grid.add(driver.id)

    if len(grid) < MIN_GRID_SIZE:
        return False

    if sprint:
        race.sprint_grid = grid
    else:
        race.starting_grid = grid
        if race.status == RaceStatus.scheduled:
            race.status = RaceStatus.qualifying_done
    # The grid is the entry list: whoever is on it is who can be bet on.
    for driver in drivers.values():
        driver.is_active = driver.id in on_grid
    return True


async def refresh_grid(
    session: AsyncSession, race: Race, collector: F1DataCollector | None = None
) -> dict[str, bool]:
    """Scrape and store the race grid (and the sprint grid on sprint weekends)."""
    own = collector is None
    collector = collector or F1DataCollector()
    out = {"race": False, "sprint": False}
    try:
        if not race.starting_grid:
            rows = await collector.fetch_starting_grid(race.season, race.round_number)
            out["race"] = await store_grid(session, race, rows)
        if race.is_sprint_weekend and not race.sprint_grid:
            rows = await collector.fetch_starting_grid(
                race.season, race.round_number, sprint=True
            )
            out["sprint"] = await store_grid(session, race, rows, sprint=True)
        if out["race"] or out["sprint"]:
            await session.commit()
            logger.info(f"Official grid stored for {race.race_name}: {out}")
    finally:
        if own:
            await collector.close()
    return out


async def maybe_refresh_grid(
    session: AsyncSession, race: Race, window: timedelta = timedelta(days=3)
) -> dict[str, bool]:
    """Refresh the grid only when the race is close and still missing one."""
    now = datetime.now(timezone.utc)
    start = race.race_date if race.race_date.tzinfo else race.race_date.replace(tzinfo=timezone.utc)
    needs = not race.starting_grid or (race.is_sprint_weekend and not race.sprint_grid)
    if not needs or not (now - timedelta(hours=6) <= start <= now + window):
        return {"race": False, "sprint": False}
    try:
        return await refresh_grid(session, race)
    except Exception as e:  # never let a scrape failure block a strategy
        logger.warning(f"Grid refresh failed for {race.race_name}: {e}")
        return {"race": False, "sprint": False}
