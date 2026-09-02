"""Strategy service — loads real DB data and runs the StrategyEngine.

This is the bridge that replaces the placeholder data in the API routers and
Telegram handlers with genuine, data-driven strategies. It degrades gracefully:
if there is no qualifying grid yet it derives a provisional grid from ELO, and
if a trained model is absent the Monte Carlo layer still produces predictions.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from functools import partial

import pandas as pd
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.kpis.circuit_kpis import CircuitKPICalculator
from app.kpis.driver_kpis import DriverKPICalculator
from app.models.circuit import Circuit
from app.models.driver import Driver
from app.models.qualifying_result import QualifyingResult
from app.models.race import Race, RaceStatus
from app.models.race_result import RaceResult
from app.models.team import Team
from app.models.weather import Weather
from app.optimizer.strategy_engine import StrategyEngine
from app.utils.guardrails import clamp_probability
from app.utils.season_window import MIN_ROWS_FOR_ERA_ONLY, seasons_in_scope

# Bounds on a caller-supplied simulation count. The upper bound keeps a single
# request from occupying a CPU indefinitely; the lower one keeps the estimates
# from being pure noise.
MIN_SIMULATIONS = 500
MAX_SIMULATIONS = 50_000

_circuit_calc = CircuitKPICalculator()
_driver_calc = DriverKPICalculator()


async def get_next_or_latest_race(session: AsyncSession) -> Race | None:
    """Pick the next scheduled race, or fall back to the most recent finished one."""
    now = datetime.now(timezone.utc)
    upcoming = (
        await session.execute(
            select(Race)
            .where(Race.race_date >= now, Race.status != RaceStatus.cancelled)
            .order_by(Race.race_date.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if upcoming:
        return upcoming
    return (
        await session.execute(
            select(Race).order_by(Race.race_date.desc()).limit(1)
        )
    ).scalar_one_or_none()


async def _grid_for_race(
    session: AsyncSession, race: Race, active_drivers: list[Driver]
) -> dict[str, int]:
    """Return {driver_id: grid_position}. Uses qualifying if available, else ELO."""
    quali = (
        await session.execute(
            select(QualifyingResult)
            .where(QualifyingResult.race_id == race.id)
            .order_by(QualifyingResult.grid_position.asc())
        )
    ).scalars().all()
    if quali:
        return {str(q.driver_id): q.grid_position for q in quali}

    # Provisional grid: rank active drivers by ELO (best ELO -> P1).
    ranked = sorted(active_drivers, key=lambda d: d.current_elo, reverse=True)
    return {str(d.id): i for i, d in enumerate(ranked, start=1)}


async def _circuit_kpis(session: AsyncSession, circuit: Circuit | None) -> dict:
    """Stored circuit KPIs augmented with live avg pit stops / tyre degradation."""
    kpis = {
        "avg_dnf_rate": (circuit.avg_dnf_rate if circuit else None) or 0.1,
        "avg_overtaking_index": (circuit.avg_overtaking_index if circuit else None) or 0.5,
        "avg_tire_degradation": (circuit.avg_tire_degradation if circuit else None) or 0.3,
        "avg_pit_stops": None,
    }
    if circuit is None:
        return kpis

    season_of_race = {
        rid: season
        for rid, season in (
            await session.execute(
                select(Race.id, Race.season).where(Race.circuit_id == circuit.id)
            )
        ).all()
    }
    if not season_of_race:
        return kpis

    all_results = (
        await session.execute(
            select(RaceResult).where(RaceResult.race_id.in_(list(season_of_race)))
        )
    ).scalars().all()

    # Same regulation-era scoping as the offline KPI build, so a live request
    # and a rebuilt Circuit row agree on which seasons count.
    rows_per_season: dict[int, int] = {}
    for r in all_results:
        s = season_of_race.get(r.race_id)
        if s is not None:
            rows_per_season[s] = rows_per_season.get(s, 0) + 1
    scope = set(
        seasons_in_scope(
            sorted(rows_per_season),
            min_rows=MIN_ROWS_FOR_ERA_ONLY,
            rows_per_season=rows_per_season,
        )
    )
    results = [r for r in all_results if season_of_race.get(r.race_id) in scope]

    if results:
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
        kpis["avg_tire_degradation"] = _circuit_calc.calculate_tire_degradation(df)
        kpis["avg_dnf_rate"] = _circuit_calc.calculate_dnf_rate(df)
        kpis["avg_overtaking_index"] = _circuit_calc.calculate_overtaking_index(df)
        pit_mean = float(df["pit_stops"].mean())
        # Only trust it as an anchor when stops were actually recorded.
        kpis["avg_pit_stops"] = pit_mean if pit_mean > 0 else None
    return kpis


async def _driver_kpis(session: AsyncSession, driver: Driver) -> dict:
    """Live per-driver KPIs from the driver's full result history."""
    results = (
        await session.execute(
            select(RaceResult).where(RaceResult.driver_id == driver.id)
        )
    ).scalars().all()
    if not results:
        return {}
    df = pd.DataFrame(
        [
            {
                "final_position": r.final_position,
                "grid_position": r.grid_position,
                "dnf": r.dnf,
                "race_id": str(r.race_id),
            }
            for r in results
        ]
    )
    return {
        "consistency_index": _driver_calc.calculate_consistency_index(df),
        "dnf_rate": _driver_calc.calculate_dnf_rate(df),
        "avg_positions_gained": _driver_calc.calculate_avg_positions_gained(df),
        "volatility": _driver_calc.calculate_performance_std(df),
        "wet_performance": 0.0,
        "momentum": 1.0,
    }


def _confidence(n_circuit_results: int, weather_risk: float) -> float:
    """Heuristic 'reg confidence': grows with circuit data, shrinks with weather risk."""
    base = min(0.9, 0.30 + 0.06 * min(n_circuit_results, 10))
    return clamp_probability(base * (1.0 - 0.3 * weather_risk))


async def build_strategy(
    session: AsyncSession,
    race: Race,
    aggressiveness: str = "balanced",
    n_simulations: int | None = None,
) -> dict:
    """Assemble all inputs from the DB and run the StrategyEngine for a race."""
    circuit = (
        await session.execute(select(Circuit).where(Circuit.id == race.circuit_id))
    ).scalar_one_or_none()

    active_drivers = (
        await session.execute(select(Driver).where(Driver.is_active == True))  # noqa: E712
    ).scalars().all()
    teams = {
        t.id: t for t in (await session.execute(select(Team))).scalars().all()
    }

    grid = await _grid_for_race(session, race, active_drivers)
    circuit_kpis = await _circuit_kpis(session, circuit)

    # Latest weather snapshot for the race, if any.
    weather_row = (
        await session.execute(
            select(Weather)
            .where(Weather.race_id == race.id)
            .order_by(Weather.timestamp.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    weather = {
        "weather_risk_index": weather_row.weather_risk_index if weather_row else 0.1,
        "rain_probability": weather_row.rain_probability if weather_row else 0.1,
        "temperature_air": weather_row.temperature_air if weather_row else 25.0,
        "humidity": weather_row.humidity if weather_row else 50.0,
    }

    drivers_payload: list[dict] = []
    team_membership: dict[str, str] = {}
    for d in active_drivers:
        team = teams.get(d.team_id)
        did = str(d.id)
        team_membership[did] = str(d.team_id)
        drivers_payload.append(
            {
                "driver_id": did,
                "team_id": str(d.team_id),
                "name": d.name,
                "code": d.code,
                "grid_position": grid.get(did, 20),
                "elo": d.current_elo,
                "team_elo": team.current_elo if team else 1500.0,
                "kpis": await _driver_kpis(session, d),
                "team_kpis": {
                    "mechanical_reliability": (team.reliability_index if team else None) or 0.95,
                    "strategic_error_rate": (team.strategic_error_rate if team else None) or 0.1,
                },
            }
        )

    n_circuit_results = 0
    if circuit_kpis.get("avg_pit_stops") is not None:
        n_circuit_results = 10  # had enough data to compute a pit anchor
    confidence = _confidence(n_circuit_results, weather["weather_risk_index"])

    engine = StrategyEngine()
    try:
        engine.xgb_model.load_model("latest")
    except Exception as e:
        logger.info(f"No trained model loaded ({e}); using Monte Carlo only")

    # Bound the request. Monte Carlo is a Python-level loop, so an unbounded
    # n_simulations lets one caller occupy a CPU for as long as it likes.
    n_sims = n_simulations or settings.monte_carlo_simulations
    n_sims = max(MIN_SIMULATIONS, min(int(n_sims), MAX_SIMULATIONS))

    # generate_strategy is CPU-bound and fully synchronous. Called inline it
    # blocked the event loop for seconds at a time — /health could not be
    # answered during a run, concurrent requests serialised, and in the bot
    # process one /simulate froze every other user. Hand it to a worker thread.
    strategy = await asyncio.to_thread(
        partial(
            engine.generate_strategy,
            drivers=drivers_payload,
            circuit_kpis=circuit_kpis,
            weather=weather,
            team_membership=team_membership,
            user_aggressiveness=aggressiveness,
            # These are two different things. `is_sprint` means "the session
            # being predicted IS the sprint race"; `is_sprint_weekend` means
            # "this GP is part of a weekend that also contains a sprint". This
            # service builds a strategy for the Grand Prix, so is_sprint is
            # always False — passing the weekend flag here doubled the GP's
            # points and clamped the pit-stop projection to the sprint window
            # (a 3-stop race reported as a 1-stopper).
            is_sprint=False,
            is_sprint_weekend=race.is_sprint_weekend,
            n_simulations=n_sims,
            confidence=confidence,
        )
    )

    strategy["race"] = {
        "id": str(race.id),
        "name": race.race_name,
        "round": race.round_number,
        "season": race.season,
        "circuit": circuit.name if circuit else "Unknown",
        "date": race.race_date.isoformat(),
        "is_sprint_weekend": race.is_sprint_weekend,
        "status": race.status.value,
    }
    strategy["weather"] = weather
    strategy["confidence"] = confidence
    return strategy
