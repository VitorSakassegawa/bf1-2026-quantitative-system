"""Walk-forward backtest of the engine under the pool's real rules.

For every finished session, in chronological order, the engine is shown only
what was known before the start (the starting grid plus all earlier results),
asked for a bet, and that bet is scored against what actually happened. This
is the honest answer to "does the model need retuning?" — it measures the
decision the system exists to make, with the scoring it is made under.

Two kinds of numbers come out:

* the **realised pool score** of the recommended bets, next to the naive
  baseline of simply betting the starting grid. Over one season this is
  noisy (roughly +/-60 points per event), so small differences mean nothing;
* **proper scoring rules** — points RMSE, DNF Brier score, and the log
  likelihood of the driver who actually finished 11th. These separate good
  probability estimates from lucky ones and are what the parameters in
  ``race_model.ModelParams`` were chosen on.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from app.elo.elo_engine import ELOEngine
from app.models_ml.race_model import (
    DEFAULT_PARAMS,
    ELO_BASELINE,
    Entry,
    FormBook,
    ModelParams,
    ObservedResult,
    simulate,
)
from app.optimizer.pool_optimizer import optimize_allocation
from app.rules.pool_rules import DEFAULT_RULES, PoolRules, ResultLine
from app.utils.season_window import crosses_era_boundary, regress_elo_for_new_era


@dataclass
class EventRow:
    driver_id: str
    team_id: str
    grid: int
    position: int | None      # None when not classified
    classified: bool
    retired: bool             # charged the pool's retirement penalty
    code: str = ""


@dataclass
class Event:
    season: int
    round: int
    sprint: bool
    date: str
    name: str
    rows: list[EventRow]

    @property
    def label(self) -> str:
        return f"{self.season} R{self.round:02d} {self.name}"


@dataclass
class EventOutcome:
    event: Event
    strategy: str
    allocation: dict[str, int]
    eleventh_pick: str | None
    score: float
    chip_score: float
    eleventh_hit: bool
    expected_score: float
    sq_error: float = 0.0
    brier: float = 0.0
    ll11: float | None = None
    n_rows: int = 0


@dataclass
class BacktestReport:
    outcomes: list[EventOutcome] = field(default_factory=list)

    def _sel(self, season: int | None = None) -> list[EventOutcome]:
        return [o for o in self.outcomes if season is None or o.event.season == season]

    def total(self, season: int | None = None) -> float:
        return sum(o.score for o in self._sel(season))

    def chip_total(self, season: int | None = None) -> float:
        return sum(o.chip_score for o in self._sel(season))

    def hits(self, season: int | None = None) -> int:
        return sum(o.eleventh_hit for o in self._sel(season))

    def events(self, season: int | None = None) -> int:
        return len(self._sel(season))

    def rmse(self, season: int | None = None) -> float:
        sel = self._sel(season)
        n = sum(o.n_rows for o in sel)
        return math.sqrt(sum(o.sq_error for o in sel) / n) if n else float("nan")

    def brier(self, season: int | None = None) -> float:
        sel = self._sel(season)
        n = sum(o.n_rows for o in sel)
        return sum(o.brier for o in sel) / n if n else float("nan")

    def ll11(self, season: int | None = None) -> float:
        vals = [o.ll11 for o in self._sel(season) if o.ll11 is not None]
        return float(np.mean(vals)) if vals else float("nan")


def build_events(
    calendar: list[dict],
    results: dict[int, list[dict]],
    sprints: dict[int, list[dict]],
    season: int,
) -> list[Event]:
    """Turn one season of collector output into chronologically ordered events."""
    by_round = {c["round"]: c for c in calendar}
    events: list[Event] = []
    for is_sprint, table in ((True, sprints), (False, results)):
        for rnd, rows in table.items():
            cal = by_round.get(rnd, {})
            start = cal.get("sprint_start") if is_sprint else cal.get("start")
            date = start.isoformat() if start else str(cal.get("date", ""))
            name = cal.get("race_name", f"Round {rnd}") + (" Sprint" if is_sprint else "")
            events.append(
                Event(
                    season=season,
                    round=rnd,
                    sprint=is_sprint,
                    date=date,
                    name=name,
                    rows=[
                        EventRow(
                            driver_id=r["driver_ref"] or r["driver_code"],
                            team_id=r["team_ref"] or r["team"],
                            grid=r["grid"],
                            position=r["position"],
                            classified=not r["dnf"],
                            retired=r["retired"],
                            code=r["driver_code"],
                        )
                        for r in rows
                    ],
                )
            )
    # Sprints run before the race of the same round.
    events.sort(key=lambda e: (e.season, e.round, not e.sprint))
    return events


def _grid_baseline(event: Event, rules: PoolRules) -> tuple[dict[str, int], str | None]:
    """Bet the starting grid: first five teams get 5-5-3-1-1, P11 on the grid."""
    order = sorted(event.rows, key=lambda r: r.grid if r.grid > 0 else 99)
    picks, teams = [], set()
    for r in order:
        if r.team_id not in teams:
            teams.add(r.team_id)
            picks.append(r.driver_id)
        if len(picks) == rules.minimum_drivers:
            break
    alloc = dict(zip(picks, rules.ideal_chip_pattern()))
    eleventh = order[10].driver_id if len(order) > 10 else None
    return alloc, eleventh


def walk_forward(
    events: list[Event],
    seasons: set[int] | None = None,
    *,
    strategy: str = "model",
    params: ModelParams = DEFAULT_PARAMS,
    rules: PoolRules = DEFAULT_RULES,
    risk_aversion: float = 0.0,
    n_simulations: int = 6000,
    seed: int = 7,
) -> BacktestReport:
    """Replay ``events`` in order; bet on those in ``seasons`` (default: all)."""
    book = FormBook(params)
    elo_engine = ELOEngine()
    elos: dict[str, float] = {}
    previous_season: int | None = None
    report = BacktestReport()

    for event in sorted(events, key=lambda e: (e.season, e.round, not e.sprint)):
        if previous_season is not None and crosses_era_boundary(previous_season, event.season):
            elos = {k: regress_elo_for_new_era(v, ELO_BASELINE) for k, v in elos.items()}
        previous_season = event.season
        book.begin(event.season)

        if seasons is None or event.season in seasons:
            entries = book.rate(
                [
                    Entry(
                        driver_id=r.driver_id,
                        team_id=r.team_id,
                        grid=r.grid if r.grid > 0 else None,
                        elo=elos.get(r.driver_id, ELO_BASELINE),
                    )
                    for r in event.rows
                ]
            )
            sim = simulate(
                entries, sprint=event.sprint, rules=rules, params=params,
                n_simulations=n_simulations, seed=seed,
            )
            team_of = {r.driver_id: r.team_id for r in event.rows}
            if strategy == "grid":
                allocation, eleventh = _grid_baseline(event, rules)
                expected = 0.0
            else:
                opt = optimize_allocation(sim, team_of, rules, risk_aversion)
                allocation, eleventh, expected = opt.allocation, opt.eleventh_pick, opt.expected_total

            lines = {
                r.driver_id: ResultLine(
                    position=r.position if r.classified else None, retired=r.retired
                )
                for r in event.rows
            }
            breakdown = rules.score(allocation, eleventh, lines, sprint=event.sprint)

            table = rules.points_table(event.sprint)
            actual = np.array(
                [
                    table[r.position - 1] if r.classified and r.position and r.position <= len(table) else 0
                    for r in event.rows
                ],
                float,
            )
            failed = np.array([0.0 if r.classified else 1.0 for r in event.rows])
            i11 = next(
                (i for i, r in enumerate(event.rows) if r.classified and r.position == 11),
                None,
            )
            report.outcomes.append(
                EventOutcome(
                    event=event,
                    strategy=strategy,
                    allocation=allocation,
                    eleventh_pick=eleventh,
                    score=breakdown.total,
                    chip_score=breakdown.total - breakdown.eleventh_bonus,
                    eleventh_hit=breakdown.eleventh_hit,
                    expected_score=expected,
                    sq_error=float(((sim.expected_points - actual) ** 2).sum()),
                    brier=float(((sim.dnf_probability - failed) ** 2).sum()),
                    ll11=float(np.log(max(sim.p11[i11], 1e-4))) if i11 is not None else None,
                    n_rows=len(event.rows),
                )
            )

        book.observe(
            event.season,
            [
                ObservedResult(r.driver_id, r.team_id, r.position, r.classified)
                for r in event.rows
            ],
            sprint=event.sprint,
        )
        if not event.sprint:
            updates = elo_engine.update_elo_race(
                [
                    {"driver_id": r.driver_id, "final_position": r.position, "dnf": not r.classified}
                    for r in event.rows
                ],
                {r.driver_id: elos.get(r.driver_id, ELO_BASELINE) for r in event.rows},
            )
            for driver_id, u in updates.items():
                elos[driver_id] = u["elo_after"]

    return report
