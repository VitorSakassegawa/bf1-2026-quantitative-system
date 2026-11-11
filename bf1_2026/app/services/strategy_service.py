"""Strategy service — loads real DB data and runs the StrategyEngine.

Everything the engine needs is assembled here from Postgres:

* the active grid and each driver's current team;
* the starting grid — the official one when it has been published, the
  qualifying order as a proxy before that, nothing at all before qualifying
  (the engine then infers a provisional grid from form);
* walk-forward form and reliability for the current regulation era, built by
  replaying every finished race and sprint into a ``FormBook``.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from functools import partial

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.kpis.circuit_kpis import CircuitKPICalculator
from app.models.circuit import Circuit
from app.models.driver import Driver
from app.models.qualifying_result import QualifyingResult
from app.models.race import Race, RaceStatus
from app.models.race_result import RaceResult
from app.models.sprint_result import SprintResult
from app.models.team import Team
from app.models.weather import Weather
from app.models_ml.race_model import DEFAULT_PARAMS, FormBook, ModelParams, ObservedResult
from app.optimizer.strategy_engine import StrategyEngine
from app.rules.pool_rules import DEFAULT_RULES, PoolRules
from app.utils.guardrails import clamp_probability
from app.utils.season_window import MIN_ROWS_FOR_ERA_ONLY, era_start_for, seasons_in_scope
from app.utils.validators import bet_deadline_for

# Bounds on a caller-supplied simulation count.
MIN_SIMULATIONS = 500
MAX_SIMULATIONS = 50_000

_circuit_calc = CircuitKPICalculator()


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
    session: AsyncSession, race: Race, sprint: bool = False
) -> tuple[dict[str, int], str]:
    """Return ({driver_id: grid_position}, source).

    ``source`` is "official" (published starting grid, penalties applied),
    "qualifying" (classification order — close, but ignores grid penalties) or
    "provisional" (nothing known yet; the engine will infer one from form).
    """
    stored = race.sprint_grid if sprint else race.starting_grid
    if stored:
        return {str(k): int(v) for k, v in stored.items()}, "official"
    if sprint:
        return {}, "provisional"

    quali = (
        await session.execute(
            select(QualifyingResult)
            .where(QualifyingResult.race_id == race.id)
            .order_by(QualifyingResult.grid_position.asc())
        )
    ).scalars().all()
    if quali:
        return {str(q.driver_id): q.grid_position for q in quali}, "qualifying"
    return {}, "provisional"


async def build_form_book(
    session: AsyncSession,
    season: int,
    before: datetime | None = None,
    params: ModelParams = DEFAULT_PARAMS,
) -> FormBook:
    """Replay the current regulation era's finished sessions into a FormBook.

    Only sessions that started before ``before`` are used, so a strategy for a
    past race is built from what was known at the time.
    """
    book = FormBook(params)
    book.begin(season)
    era_start = era_start_for(season)

    race_filter = [Race.season >= era_start, Race.season <= season]
    if before is not None:
        race_filter.append(Race.race_date < before)
    races = (
        await session.execute(select(Race).where(*race_filter).order_by(Race.race_date))
    ).scalars().all()
    if not races:
        return book
    race_ids = [r.id for r in races]

    by_race: dict = {rid: [] for rid in race_ids}
    sprint_by_race: dict = {rid: [] for rid in race_ids}
    for model, bucket in ((RaceResult, by_race), (SprintResult, sprint_by_race)):
        rows = (
            await session.execute(
                select(
                    model.race_id, model.driver_id, model.team_id,
                    model.final_position, model.dnf,
                ).where(model.race_id.in_(race_ids))
            )
        ).all()
        for race_id, driver_id, team_id, position, dnf in rows:
            bucket[race_id].append(
                ObservedResult(
                    driver_id=str(driver_id),
                    team_id=str(team_id) if team_id else "",
                    position=position,
                    classified=not dnf and position is not None,
                )
            )

    for race in races:
        if sprint_by_race[race.id]:
            book.observe(race.season, sprint_by_race[race.id], sprint=True)
        if by_race[race.id]:
            book.observe(race.season, by_race[race.id], sprint=False)
    return book


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


def _confidence(n_circuit_results: int, weather_risk: float) -> float:
    """Heuristic 'reg confidence': grows with circuit data, shrinks with weather risk."""
    base = min(0.9, 0.30 + 0.06 * min(n_circuit_results, 10))
    return clamp_probability(base * (1.0 - 0.3 * weather_risk))


async def build_strategy(
    session: AsyncSession,
    race: Race,
    aggressiveness: str = "balanced",
    n_simulations: int | None = None,
    sprint: bool = False,
    rules: PoolRules | None = None,
) -> dict:
    """Assemble all inputs from the DB and run the StrategyEngine for a session.

    ``sprint=True`` builds the bet for the weekend's sprint instead of the
    Grand Prix — the pool scores them as separate events.
    """
    rules = rules or DEFAULT_RULES
    circuit = (
        await session.execute(select(Circuit).where(Circuit.id == race.circuit_id))
    ).scalar_one_or_none()

    active_drivers = (
        await session.execute(select(Driver).where(Driver.is_active == True))  # noqa: E712
    ).scalars().all()
    teams = {t.id: t for t in (await session.execute(select(Team))).scalars().all()}

    grid, grid_source = await _grid_for_race(session, race, sprint)
    if grid_source == "official":
        # A published grid is the entry list: bet on who is actually starting,
        # which is how a stand-in driver gets picked up.
        on_grid = (
            await session.execute(
                select(Driver).where(Driver.id.in_([uuid.UUID(k) for k in grid]))
            )
        ).scalars().all()
        if len(on_grid) >= 10:
            active_drivers = on_grid
    circuit_kpis = await _circuit_kpis(session, circuit)
    book = await build_form_book(session, race.season, before=race.race_date)

    weather_row = (
        await session.execute(
            select(Weather)
            .where(Weather.race_id == race.id)
            .order_by(Weather.timestamp.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    weather = {
        "weather_risk_index": weather_row.weather_risk_index if weather_row else 0.0,
        "rain_probability": weather_row.rain_probability if weather_row else 0.0,
        "temperature_air": weather_row.temperature_air if weather_row else None,
        "humidity": weather_row.humidity if weather_row else None,
        "available": weather_row is not None,
    }

    n = len(active_drivers)
    drivers_payload: list[dict] = []
    team_membership: dict[str, str] = {}
    for d in active_drivers:
        team = teams.get(d.team_id)
        did, tid = str(d.id), str(d.team_id)
        team_membership[did] = tid
        position = grid.get(did)
        drivers_payload.append(
            {
                "driver_id": did,
                "team_id": tid,
                "team_name": team.name if team else None,
                "name": d.name,
                "code": d.code,
                "grid_position": position,
                "elo": d.current_elo,
                "form": book.form(did, tid, float(position) if position else float(n)),
                "dnf_probability": book.dnf_probability(did, tid),
            }
        )

    n_circuit_results = 10 if circuit_kpis.get("avg_pit_stops") is not None else 0
    confidence = _confidence(n_circuit_results, weather["weather_risk_index"])

    n_sims = n_simulations or settings.monte_carlo_simulations
    n_sims = max(MIN_SIMULATIONS, min(int(n_sims), MAX_SIMULATIONS))

    engine = StrategyEngine(rules=rules)
    # The simulation is vectorised and takes tens of milliseconds, but it is
    # still CPU work — keep it off the event loop.
    strategy = await asyncio.to_thread(
        partial(
            engine.generate_strategy,
            drivers=drivers_payload,
            circuit_kpis=circuit_kpis,
            weather=weather,
            team_membership=team_membership,
            user_aggressiveness=aggressiveness,
            # `is_sprint`: the session being bet on IS the sprint (sprint
            # points table, doubled score). `is_sprint_weekend` only tells the
            # pit-stop projection that tyre sets are tighter.
            is_sprint=sprint,
            is_sprint_weekend=race.is_sprint_weekend,
            n_simulations=n_sims,
            confidence=confidence,
        )
    )

    start = (race.sprint_date if sprint else race.race_date) or race.race_date
    deadline = start if sprint else bet_deadline_for(race.race_date, race.deadline_bets)
    strategy["race"] = {
        "id": str(race.id),
        "name": race.race_name + (" — Sprint" if sprint else ""),
        "round": race.round_number,
        "season": race.season,
        "circuit": circuit.name if circuit else "Unknown",
        "date": start.isoformat(),
        "deadline": deadline.isoformat(),
        "is_sprint_weekend": race.is_sprint_weekend,
        "session": "sprint" if sprint else "race",
        "status": race.status.value,
    }
    strategy["grid_source"] = grid_source
    # Who has actually finished 11th this era — context for the 11th-place
    # pick, which is close to a lottery on the model's numbers alone.
    counts = book.eleventh_counts()
    by_id = {str(d.id): d for d in active_drivers}
    strategy["eleventh_history"] = [
        {"driver_code": by_id[did].code, "driver_id": did, "times": n}
        for did, n in sorted(counts.items(), key=lambda kv: -kv[1])
        if did in by_id and n > 0
    ][:5]
    for cand in strategy.get("eleventh_candidates", []):
        cand["times_eleventh"] = counts.get(cand["driver_id"], 0)
    strategy["form_events"] = book.events_seen
    strategy["league_dnf_rate"] = round(book.league_dnf_rate(), 4)
    strategy["weather"] = weather
    strategy["confidence"] = confidence
    return strategy
