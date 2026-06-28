"""Ingest historical F1 data (calendar, qualifying, race & sprint results).

Pulls from the Ergast/OpenF1 fallback collector and writes circuits, races,
qualifying_results, race_results and sprint_results into Postgres.

Idempotent at the race level: a (season, round) already present is skipped, so
re-running only fills gaps.

    python -m scripts.ingest 2021 2025      # inclusive range
    python -m scripts.ingest                # defaults to last 5 seasons
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select

from app.collectors.f1_api_fallback import F1ApiFallback
from app.database import AsyncSessionLocal
from app.models.circuit import Circuit, TrackType
from app.models.driver import Driver
from app.models.qualifying_result import QualifyingResult
from app.models.race import Race, RaceStatus
from app.models.race_result import RaceResult
from app.models.sprint_result import SprintResult
from app.models.team import Team


async def _get_or_create_team(session, name: str) -> Team:
    if not name:
        name = "Unknown"
    result = await session.execute(select(Team).where(Team.name == name))
    team = result.scalar_one_or_none()
    if team is None:
        team = Team(name=name, full_name=name, is_active=False)
        session.add(team)
        await session.flush()
    return team


async def _get_or_create_driver(session, code: str, name: str, team: Team) -> Driver | None:
    if not code:
        return None
    result = await session.execute(select(Driver).where(Driver.code == code))
    driver = result.scalar_one_or_none()
    if driver is None:
        # Historical driver no longer on the grid: create as inactive.
        driver = Driver(
            name=name or code,
            code=code,
            number=0,
            nationality="Unknown",
            team_id=team.id,
            is_active=False,
        )
        session.add(driver)
        await session.flush()
        logger.debug(f"Created historical driver {code}")
    return driver


async def _get_or_create_circuit(session, name: str, country: str, lat: float, lon: float) -> Circuit:
    result = await session.execute(select(Circuit).where(Circuit.name == name))
    circuit = result.scalar_one_or_none()
    if circuit is None:
        circuit = Circuit(
            name=name or "Unknown Circuit",
            country=country or "Unknown",
            city=country or "Unknown",
            track_type=TrackType.mixed,
            latitude=lat or None,
            longitude=lon or None,
        )
        session.add(circuit)
        await session.flush()
        logger.info(f"Created circuit {name}")
    return circuit


async def _race_exists(session, season: int, rnd: int) -> bool:
    result = await session.execute(
        select(Race.id).where(Race.season == season, Race.round_number == rnd)
    )
    return result.scalar_one_or_none() is not None


def _parse_date(date_str: str) -> datetime:
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


async def ingest_season(collector: F1ApiFallback, season: int) -> None:
    calendar = await collector.fetch_calendar(season)
    if not calendar:
        logger.warning(f"No calendar for {season}")
        return

    for race in calendar:
        rnd = race["round"]
        async with AsyncSessionLocal() as session:
            if await _race_exists(session, season, rnd):
                logger.debug(f"{season} R{rnd} already ingested, skipping")
                continue

            circuit = await _get_or_create_circuit(
                session,
                race.get("circuit", race.get("race_name", "")),
                race.get("country", ""),
                race.get("latitude", 0.0),
                race.get("longitude", 0.0),
            )

            race_row = Race(
                circuit_id=circuit.id,
                season=season,
                round_number=rnd,
                race_name=race.get("race_name", f"Round {rnd}"),
                race_date=_parse_date(race.get("date", "")),
                is_sprint_weekend=race.get("is_sprint", False),
                status=RaceStatus.finished,
            )
            session.add(race_row)
            await session.flush()

            # --- Pit stop counts (per driver, matched by Ergast driverId) ---
            pit_counts: dict[str, int] = {}
            for p in await collector.fetch_pit_stops(season, rnd):
                ref = p.get("driver", "")
                if ref:
                    pit_counts[ref] = pit_counts.get(ref, 0) + 1

            # --- Race results ---
            results = await collector.fetch_race_results(season, rnd)
            for r in results:
                team = await _get_or_create_team(session, r.get("team", ""))
                driver = await _get_or_create_driver(
                    session, r.get("driver_code", ""), r.get("driver", ""), team
                )
                if driver is None:
                    continue
                session.add(
                    RaceResult(
                        race_id=race_row.id,
                        driver_id=driver.id,
                        final_position=r.get("position"),
                        grid_position=r.get("grid", 0) or 0,
                        points_scored=r.get("points", 0.0),
                        fastest_lap=r.get("fastest_lap", False),
                        dnf=r.get("dnf", False),
                        dnf_reason=r.get("dnf_reason"),
                        laps_completed=r.get("laps", 0),
                        pit_stops=pit_counts.get(r.get("driver_ref", ""), 0),
                    )
                )

            # --- Qualifying results ---
            quali = await collector.fetch_qualifying_results(season, rnd)
            for q in quali:
                res = await session.execute(
                    select(Driver).where(Driver.code == q.get("driver_code", ""))
                )
                driver = res.scalar_one_or_none()
                if driver is None:
                    continue
                session.add(
                    QualifyingResult(
                        race_id=race_row.id,
                        driver_id=driver.id,
                        grid_position=q.get("position", 0) or 0,
                        q1_time=q.get("q1_time"),
                        q2_time=q.get("q2_time"),
                        q3_time=q.get("q3_time"),
                    )
                )

            # --- Sprint results (only on sprint weekends) ---
            if race.get("is_sprint"):
                sprint = await collector.fetch_sprint_results(season, rnd)
                for s in sprint:
                    res = await session.execute(
                        select(Driver).where(Driver.code == s.get("driver_code", ""))
                    )
                    driver = res.scalar_one_or_none()
                    if driver is None:
                        continue
                    session.add(
                        SprintResult(
                            race_id=race_row.id,
                            driver_id=driver.id,
                            final_position=s.get("position"),
                            grid_position=s.get("position", 0) or 0,
                            points_scored=s.get("points", 0.0),
                            dnf=s.get("dnf", False),
                        )
                    )

            await session.commit()
            logger.info(f"Ingested {season} R{rnd}: {race_row.race_name}")


async def ingest(start: int, end: int) -> None:
    collector = F1ApiFallback()
    try:
        for season in range(start, end + 1):
            logger.info(f"=== Ingesting season {season} ===")
            await ingest_season(collector, season)
    finally:
        await collector.close()
    logger.info(f"Ingestion complete: {start}-{end}")


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        start_year, end_year = int(sys.argv[1]), int(sys.argv[2])
    else:
        current = datetime.now().year
        start_year, end_year = current - 5, current - 1
    asyncio.run(ingest(start_year, end_year))
