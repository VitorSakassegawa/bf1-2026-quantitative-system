"""Sync calendar and results from the F1 results API into Postgres.

    python -m scripts.ingest                  # current regulation era -> now
    python -m scripts.ingest 2022 2026        # inclusive range
    python -m scripts.ingest 2026 --refresh   # re-download even stored rounds

This is a *sync*, not a one-shot import: every run brings the database in line
with the API for the seasons asked for.

* the calendar is upserted by (season, round) — dates, start times, sprint
  flags and circuits follow the API, so a rescheduled or added race needs no
  code change;
* a finished round with no stored results gets them; rounds already stored
  are left alone unless ``--refresh`` is given;
* the active roster is rewritten from the latest entry list.

Two bugs this replaces. The previous version skipped any (season, round) that
already existed — but the seed creates every race of the current season up
front, so **no result of the season in progress was ever stored**. And its
default range ended at last year, so the current season was not even asked
for.

Requests are made per season, not per race (5 paginated calls for a season of
results instead of ~22), which keeps a five-season sync around 180 requests —
inside the API's 500-per-hour limit.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import delete, func, select

from app.collectors.f1_api_fallback import F1ApiFallback
from app.database import AsyncSessionLocal
from app.models.driver import Driver
from app.models.qualifying_result import QualifyingResult
from app.models.race import Race, RaceStatus
from app.models.race_result import RaceResult
from app.models.sprint_result import SprintResult
from app.models.team import Team
from app.utils.season_window import current_season, era_start_for
from scripts.seed import upsert_circuit, upsert_race

_F1_SUFFIXES = (" F1 Team", " Racing", " Formula 1 Team")


async def _get_or_create_team(session, name: str, ref: str | None = None) -> Team:
    name = name or "Unknown"
    team = None
    if ref:
        team = (await session.execute(select(Team).where(Team.ref == ref))).scalar_one_or_none()
    if team is None:
        candidates = [name] + [name[: -len(s)] for s in _F1_SUFFIXES if name.endswith(s)]
        for candidate in candidates:
            team = (
                await session.execute(select(Team).where(Team.name == candidate))
            ).scalar_one_or_none()
            if team is not None:
                break
        if team is not None and ref and team.ref and team.ref != ref:
            team = None     # same display name, different constructor
    if team is None:
        team = Team(name=name, full_name=name, ref=ref or None, is_active=False)
        session.add(team)
        await session.flush()
    elif ref and not team.ref:
        team.ref = ref
    return team


async def _get_or_create_driver(session, row: dict, team: Team) -> Driver | None:
    """Match on the API's driverId, then on the 3-letter code."""
    ref, code = row.get("driver_ref") or None, row.get("driver_code") or ""
    if not ref and not code:
        return None
    driver = None
    if ref:
        driver = (
            await session.execute(select(Driver).where(Driver.ref == ref))
        ).scalar_one_or_none()
    if driver is None and code:
        driver = (
            await session.execute(select(Driver).where(Driver.code == code))
        ).scalar_one_or_none()
        if driver is not None and ref and driver.ref and driver.ref != ref:
            # Another driver already owns this code (codes get reused).
            logger.warning(f"Code {code} belongs to {driver.ref}; skipping {ref}")
            return None
    if driver is None:
        driver = Driver(
            name=row.get("driver") or code,
            code=code[:3],
            ref=ref,
            number=row.get("driver_number") or 0,
            nationality=row.get("nationality") or "Unknown",
            team_id=team.id,
            is_active=False,
        )
        session.add(driver)
        await session.flush()
        logger.debug(f"Created driver {code} ({ref})")
    elif ref and not driver.ref:
        driver.ref = ref
    return driver


async def _count(session, model, race_id) -> int:
    return (
        await session.scalar(
            select(func.count()).select_from(model).where(model.race_id == race_id)
        )
    ) or 0


async def _needs_results(session, race: Race, refresh: bool) -> bool:
    if refresh:
        return True
    total = await _count(session, RaceResult, race.id)
    if total == 0:
        return True
    # Rows written before team attribution existed are re-synced once.
    missing_team = await session.scalar(
        select(func.count())
        .select_from(RaceResult)
        .where(RaceResult.race_id == race.id, RaceResult.team_id.is_(None))
    )
    return bool(missing_team)


async def sync_season(
    collector: F1ApiFallback,
    season: int,
    refresh: bool = False,
    with_pit_stops: bool = True,
) -> dict:
    """Bring one season in line with the API. Returns counters for logging."""
    stats = {"races": 0, "results": 0, "sprints": 0, "qualifying": 0}
    calendar = await collector.fetch_calendar(season)
    if not calendar:
        logger.warning(f"No calendar for {season}")
        return stats

    results = await collector.fetch_season_results(season)
    sprints = await collector.fetch_season_sprints(season) if any(
        c["is_sprint"] for c in calendar
    ) else {}
    qualifying = await collector.fetch_season_qualifying(season)

    for cal in calendar:
        rnd = cal["round"]
        start = cal.get("start")
        if start is None:
            logger.warning(
                f"{season} R{rnd}: unparseable date {cal.get('date')!r} — "
                "skipping rather than corrupting the chronology."
            )
            continue

        async with AsyncSessionLocal() as session:
            circuit = await upsert_circuit(
                session,
                cal.get("circuit_ref") or None,
                cal.get("circuit") or cal.get("race_name", ""),
                cal.get("locality", ""),
                cal.get("country", ""),
                cal.get("latitude"),
                cal.get("longitude"),
            )
            race, _ = await upsert_race(
                session, season, rnd,
                name=cal.get("race_name") or f"Round {rnd}",
                start=start, circuit=circuit,
                sprint_start=cal.get("sprint_start") if cal.get("is_sprint") else None,
                qualifying_start=cal.get("qualifying_start"),
            )
            stats["races"] += 1

            # ---- race results ------------------------------------------- #
            rows = results.get(rnd) or []
            if rows and await _needs_results(session, race, refresh):
                pit_counts: dict[str, int] = {}
                if with_pit_stops:
                    for p in await collector.fetch_pit_stops(season, rnd):
                        if p.get("driver"):
                            pit_counts[p["driver"]] = pit_counts.get(p["driver"], 0) + 1

                await session.execute(delete(RaceResult).where(RaceResult.race_id == race.id))
                grid: dict[str, int] = {}
                for r in rows:
                    team = await _get_or_create_team(session, r["team"], r["team_ref"])
                    driver = await _get_or_create_driver(session, r, team)
                    if driver is None:
                        continue
                    session.add(
                        RaceResult(
                            race_id=race.id,
                            driver_id=driver.id,
                            team_id=team.id,
                            final_position=r["position"],
                            grid_position=r["grid"],
                            points_scored=r["points"],
                            fastest_lap=r["fastest_lap"],
                            dnf=r["dnf"],
                            dnf_reason=r["status"] if r["dnf"] else None,
                            laps_completed=r["laps"],
                            pit_stops=pit_counts.get(r["driver_ref"], 0),
                        )
                    )
                    if r["grid"] > 0:
                        grid[str(driver.id)] = r["grid"]
                race.starting_grid = grid or race.starting_grid
                race.status = RaceStatus.finished
                stats["results"] += 1

            # ---- sprint results ----------------------------------------- #
            srows = sprints.get(rnd) or []
            if srows and (refresh or await _count(session, SprintResult, race.id) == 0):
                await session.execute(
                    delete(SprintResult).where(SprintResult.race_id == race.id)
                )
                sgrid: dict[str, int] = {}
                for r in srows:
                    team = await _get_or_create_team(session, r["team"], r["team_ref"])
                    driver = await _get_or_create_driver(session, r, team)
                    if driver is None:
                        continue
                    session.add(
                        SprintResult(
                            race_id=race.id,
                            driver_id=driver.id,
                            team_id=team.id,
                            final_position=r["position"],
                            grid_position=r["grid"],
                            points_scored=r["points"],
                            dnf=r["dnf"],
                            dnf_reason=r["status"] if r["dnf"] else None,
                        )
                    )
                    if r["grid"] > 0:
                        sgrid[str(driver.id)] = r["grid"]
                race.sprint_grid = sgrid or race.sprint_grid
                stats["sprints"] += 1

            # ---- qualifying --------------------------------------------- #
            qrows = qualifying.get(rnd) or []
            if qrows and (refresh or await _count(session, QualifyingResult, race.id) == 0):
                await session.execute(
                    delete(QualifyingResult).where(QualifyingResult.race_id == race.id)
                )
                for q in qrows:
                    team = await _get_or_create_team(session, q["team"], q["team_ref"])
                    driver = await _get_or_create_driver(session, q, team)
                    if driver is None:
                        continue
                    session.add(
                        QualifyingResult(
                            race_id=race.id,
                            driver_id=driver.id,
                            grid_position=q["position"],
                            q1_time=q["q1_time"],
                            q2_time=q["q2_time"],
                            q3_time=q["q3_time"],
                        )
                    )
                if race.status == RaceStatus.scheduled:
                    race.status = RaceStatus.qualifying_done
                stats["qualifying"] += 1

            await session.commit()

    # Rounds that are no longer on the calendar (a cancelled race) and never
    # ran are marked cancelled rather than left as phantom fixtures.
    last_round = max(c["round"] for c in calendar)
    async with AsyncSessionLocal() as session:
        stale = (
            await session.execute(
                select(Race).where(Race.season == season, Race.round_number > last_round)
            )
        ).scalars().all()
        for race in stale:
            if await _count(session, RaceResult, race.id) == 0:
                race.status = RaceStatus.cancelled
        await session.commit()

    logger.info(
        f"Synced {season}: {stats['races']} rounds on the calendar, "
        f"{stats['results']} new result sets, {stats['sprints']} sprints, "
        f"{stats['qualifying']} qualifying sessions"
    )
    return stats


async def sync_roster() -> int:
    """Make the active grid match the most recent entry list.

    The seed's grid is a snapshot. Drivers change mid-season (in 2026 Haas
    alone ran three different second drivers), so after every sync the active
    flag and each driver's team are taken from whoever took part in the latest
    finished race. Returns the number of active drivers.
    """
    async with AsyncSessionLocal() as session:
        latest = (
            await session.execute(
                select(Race)
                .join(RaceResult, RaceResult.race_id == Race.id)
                .order_by(Race.race_date.desc())
                .limit(1)
            )
        ).scalars().first()
        if latest is None:
            return 0
        entries = (
            await session.execute(
                select(RaceResult.driver_id, RaceResult.team_id).where(
                    RaceResult.race_id == latest.id
                )
            )
        ).all()
        if len(entries) < 10:      # not a believable entry list
            return 0
        team_of = {d: t for d, t in entries}

        for driver in (await session.execute(select(Driver))).scalars().all():
            if driver.id in team_of:
                driver.is_active = True
                if team_of[driver.id] is not None:
                    driver.team_id = team_of[driver.id]
            else:
                driver.is_active = False
        active_teams = {t for t in team_of.values() if t is not None}
        for team in (await session.execute(select(Team))).scalars().all():
            team.is_active = team.id in active_teams
        await session.commit()
        logger.info(
            f"Roster synced from {latest.season} R{latest.round_number} "
            f"{latest.race_name}: {len(team_of)} active drivers"
        )
        return len(team_of)


async def ingest(start: int, end: int, refresh: bool = False) -> None:
    collector = F1ApiFallback()
    try:
        for season in range(start, end + 1):
            logger.info(f"=== Syncing season {season} ===")
            await sync_season(collector, season, refresh=refresh)
    finally:
        await collector.close()
    await sync_roster()
    logger.info(f"Sync complete: {start}-{end}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    this_year = current_season(datetime.now(timezone.utc))
    if len(args) >= 2:
        start_year, end_year = int(args[0]), int(args[1])
    elif len(args) == 1:
        start_year = end_year = int(args[0])
    else:
        # Everything the model uses: the current regulation era plus the
        # tail of the previous one, for ratings.
        start_year, end_year = min(era_start_for(this_year), this_year - 4), this_year
    asyncio.run(ingest(start_year, end_year, refresh="--refresh" in sys.argv))
