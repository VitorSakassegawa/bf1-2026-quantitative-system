"""Build derived intelligence from ingested results.

1. Replays every race in chronological order to compute driver ELO ratings,
   writing an elo_history row per race and updating Driver.current_elo.
2. Aggregates circuit-level KPIs and persists them on the Circuit rows.
3. Aggregates team-level KPIs (reliability, strategic error rate) onto Teams.

Safe to re-run: ELO is recomputed from BASE_ELO each time, so the result is
deterministic regardless of how many times it runs.

    python -m scripts.build_intelligence
"""

from __future__ import annotations

import asyncio
import uuid

import pandas as pd
from loguru import logger
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.elo.elo_engine import ELOEngine
from app.kpis.circuit_kpis import CircuitKPICalculator
from app.kpis.team_kpis import TeamKPICalculator
from app.models.circuit import Circuit
from app.models.driver import Driver
from app.models.elo_history import ELOHistory, EntityType
from app.models.race import Race
from app.models.race_result import RaceResult
from app.models.team import Team


async def compute_elo() -> None:
    engine = ELOEngine()
    async with AsyncSessionLocal() as session:
        # Reset all drivers to base ELO for a deterministic replay.
        drivers = (await session.execute(select(Driver))).scalars().all()
        elos: dict[str, float] = {}
        for d in drivers:
            d.current_elo = ELOEngine.BASE_ELO
            elos[str(d.id)] = ELOEngine.BASE_ELO

        # Clear prior history so re-runs don't stack duplicates.
        for h in (await session.execute(select(ELOHistory))).scalars().all():
            await session.delete(h)
        await session.flush()

        races = (
            await session.execute(select(Race).order_by(Race.race_date))
        ).scalars().all()

        for race in races:
            results = (
                await session.execute(
                    select(RaceResult).where(RaceResult.race_id == race.id)
                )
            ).scalars().all()
            if not results:
                continue

            driver_results = [
                {
                    "driver_id": str(r.driver_id),
                    "final_position": r.final_position,
                    "dnf": r.dnf,
                }
                for r in results
            ]
            updates = engine.update_elo_race(driver_results, elos)

            for driver_id, u in updates.items():
                elos[driver_id] = u["elo_after"]
                session.add(
                    ELOHistory(
                        entity_type=EntityType.driver,
                        entity_id=uuid.UUID(driver_id),
                        race_id=race.id,
                        elo_before=u["elo_before"],
                        elo_after=u["elo_after"],
                        elo_delta=u["elo_delta"],
                    )
                )

        # Persist final ELO onto drivers.
        for d in drivers:
            if str(d.id) in elos:
                d.current_elo = elos[str(d.id)]

        await session.commit()
    logger.info(f"ELO computed across {len(races)} races for {len(drivers)} drivers")


async def compute_circuit_kpis() -> None:
    calc = CircuitKPICalculator()
    async with AsyncSessionLocal() as session:
        circuits = (await session.execute(select(Circuit))).scalars().all()
        for circuit in circuits:
            races = (
                await session.execute(
                    select(Race).where(Race.circuit_id == circuit.id)
                )
            ).scalars().all()
            race_ids = [r.id for r in races]
            if not race_ids:
                continue

            results = (
                await session.execute(
                    select(RaceResult).where(RaceResult.race_id.in_(race_ids))
                )
            ).scalars().all()
            if not results:
                continue

            df = pd.DataFrame(
                [
                    {
                        "final_position": r.final_position,
                        "grid_position": r.grid_position,
                        "dnf": r.dnf,
                        "pit_stops": r.pit_stops,
                    }
                    for r in results
                ]
            )
            kpis = calc.calculate_all(df)
            circuit.avg_overtaking_index = kpis["avg_overtaking_index"]
            circuit.avg_winner_grid_position = kpis["avg_winner_grid_position"]
            circuit.avg_safety_cars = kpis["avg_safety_cars"]
            circuit.avg_dnf_rate = kpis["avg_dnf_rate"]
            circuit.avg_tire_degradation = kpis["avg_tire_degradation"]

        await session.commit()
    logger.info(f"Circuit KPIs computed for {len(circuits)} circuits")


async def compute_team_kpis() -> None:
    calc = TeamKPICalculator()
    async with AsyncSessionLocal() as session:
        teams = (await session.execute(select(Team))).scalars().all()
        for team in teams:
            drivers = (
                await session.execute(
                    select(Driver).where(Driver.team_id == team.id)
                )
            ).scalars().all()
            driver_ids = [d.id for d in drivers]
            if not driver_ids:
                continue

            results = (
                await session.execute(
                    select(RaceResult).where(RaceResult.driver_id.in_(driver_ids))
                )
            ).scalars().all()
            if not results:
                continue

            df = pd.DataFrame(
                [
                    {
                        "final_position": r.final_position,
                        "grid_position": r.grid_position,
                        "dnf": r.dnf,
                        "dnf_reason": r.dnf_reason,
                        "pit_stops": r.pit_stops,
                    }
                    for r in results
                ]
            )
            team.reliability_index = calc.calculate_mechanical_reliability(df)
            team.strategic_error_rate = calc.calculate_strategic_error_rate(df)

        await session.commit()
    logger.info(f"Team KPIs computed for {len(teams)} teams")


async def build() -> None:
    await compute_elo()
    await compute_circuit_kpis()
    await compute_team_kpis()
    logger.info("Intelligence build complete")


if __name__ == "__main__":
    asyncio.run(build())
