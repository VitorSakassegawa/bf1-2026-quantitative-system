"""Exact chip allocation under the pool's real rules.

The pool's score is linear in chips with a fixed cost per selected driver:

    score = sum(chips_i * points_i) - penalty * sum(retired_i)  [+ 11th bonus]

so the expected-value optimum can be found exactly with a small dynamic
programme over teams (at most one driver per team) instead of the fixed chip
patterns the old optimiser applied. For a linear objective the answer is
always the 5-5-3-1-1 shape — which is also how the pool computes its own
"maximum points" figure — and the question is only *who* gets the 5s.

Risk profiles
-------------
Over a 27-event season the total is what counts, so maximum expected value is
the right default. A profile only changes the objective to

    mean - risk_aversion * standard deviation

and is meant for the end of a season: protect a lead (positive) or chase one
(negative). The backtest prices what hedging costs: spreading chips the way
the old "balanced" profile did scored 4,356 on the 2026 allocation against
5,076 for its own most concentrated profile.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.models_ml.race_model import SimulationResult
from app.rules.pool_rules import DEFAULT_RULES, PoolRules

# mean - k * std. Zero is the expected-value optimum.
RISK_PROFILES: dict[str, float] = {
    "conservative": 0.25,
    "balanced": 0.0,
    "aggressive": -0.15,
    "ultra_aggressive": -0.35,
}

_SEARCH_SIMS = 4000   # simulations used to price candidate moves
_MAX_PASSES = 60


@dataclass
class AllocationResult:
    allocation: dict[str, int]
    expected_score: float            # chips and penalties, sprint multiplier applied
    std: float
    p10: float
    p50: float
    p90: float
    eleventh_pick: str | None
    eleventh_probability: float
    eleventh_candidates: list[tuple[str, float]] = field(default_factory=list)
    expected_total: float = 0.0      # expected_score + expected 11th-place bonus
    risk_aversion: float = 0.0
    errors: list[str] = field(default_factory=list)


def exact_ev_allocation(
    driver_ids: list[str],
    expected_points: list[float],
    retire_probability: list[float],
    team_of: dict[str, str],
    rules: PoolRules = DEFAULT_RULES,
) -> dict[int, int] | None:
    """Exact maximum of expected score. Returns {driver index: chips} or None.

    Works from plain per-driver expectations, so it can be used without a
    simulation (e.g. on predictions that came from somewhere else).
    """
    mu = [float(x) for x in expected_points]
    fixed = [-rules.retirement_penalty * float(p) for p in retire_probability]

    groups: dict[str, list[int]] = {}
    for i, d in enumerate(driver_ids):
        team = team_of.get(d)
        if not team:
            continue        # a driver with no known team cannot satisfy the rules
        key = team if not rules.allow_same_team else f"__{i}"
        groups.setdefault(key, []).append(i)

    need, total, cap = rules.minimum_drivers, rules.total_chips, rules.max_chips_per_driver
    # state: (chips used, drivers picked capped at `need`) -> (value, picks)
    best: dict[tuple[int, int], tuple[float, tuple]] = {(0, 0): (0.0, ())}
    for members in groups.values():
        nxt = dict(best)
        for (used, k), (val, picks) in best.items():
            for i in members:
                for chips in range(1, cap + 1):
                    if used + chips > total:
                        break
                    key = (used + chips, min(k + 1, need))
                    cand = val + chips * mu[i] + fixed[i]
                    if key not in nxt or cand > nxt[key][0]:
                        nxt[key] = (cand, picks + ((i, chips),))
        best = nxt
    hit = best.get((total, need))
    return dict(hit[1]) if hit else None


def _ev_optimum(
    sim: SimulationResult, team_of: dict[str, str], rules: PoolRules
) -> dict[int, int] | None:
    return exact_ev_allocation(
        sim.driver_ids,
        sim.expected_points.tolist(),
        sim.retire_probability.tolist(),
        team_of,
        rules,
    )


def _portfolio(sim: SimulationResult, chips: np.ndarray, rules: PoolRules, rows=None) -> np.ndarray:
    """Per-simulation score of an allocation (11th-place bonus excluded)."""
    pts = sim.points if rows is None else sim.points[rows]
    ret = sim.retired if rows is None else sim.retired[rows]
    selected = chips > 0
    score = pts[:, selected] @ chips[selected].astype(np.float32)
    score = score - rules.retirement_penalty * ret[:, selected].sum(axis=1)
    return score * rules.multiplier(sim.sprint)


def _utility(scores: np.ndarray, risk_aversion: float) -> float:
    return float(scores.mean() - risk_aversion * scores.std())


def _valid(chips: np.ndarray, teams: list[str | None], rules: PoolRules) -> bool:
    sel = np.flatnonzero(chips)
    if len(sel) < rules.minimum_drivers or chips.sum() != rules.total_chips:
        return False
    if chips.max() > rules.max_chips_per_driver:
        return False
    used = [teams[i] for i in sel]
    if any(t is None for t in used):
        return False
    if not rules.allow_same_team and len(set(used)) != len(used):
        return False
    return len(set(used)) >= rules.minimum_drivers


def _local_search(
    sim: SimulationResult,
    start: np.ndarray,
    teams: list[str | None],
    rules: PoolRules,
    risk_aversion: float,
) -> np.ndarray:
    """Hill-climb on mean - k*std from a valid start, one small move at a time."""
    n = len(teams)
    rows = None
    if sim.n_simulations > _SEARCH_SIMS:
        rows = np.linspace(0, sim.n_simulations - 1, _SEARCH_SIMS).astype(int)

    def value(c: np.ndarray) -> float:
        return _utility(_portfolio(sim, c, rules, rows), risk_aversion)

    current, current_val = start.copy(), value(start)
    for _ in range(_MAX_PASSES):
        best_move, best_val = None, current_val + 1e-9
        sel = np.flatnonzero(current)
        for i in sel:
            # move one chip between two selected drivers
            for j in sel:
                if i == j or current[i] <= 1 or current[j] >= rules.max_chips_per_driver:
                    continue
                cand = current.copy()
                cand[i] -= 1
                cand[j] += 1
                v = value(cand)
                if v > best_val:
                    best_move, best_val = cand, v
            # swap a selected driver for one that is not
            for k in range(n):
                if current[k] or teams[k] is None:
                    continue
                cand = current.copy()
                cand[k], cand[i] = cand[i], 0
                if _valid(cand, teams, rules):
                    v = value(cand)
                    if v > best_val:
                        best_move, best_val = cand, v
            # bring in an extra driver with a chip taken from this one
            if current[i] > 1:
                for k in range(n):
                    if current[k] or teams[k] is None:
                        continue
                    cand = current.copy()
                    cand[i] -= 1
                    cand[k] = 1
                    if _valid(cand, teams, rules):
                        v = value(cand)
                        if v > best_val:
                            best_move, best_val = cand, v
            # drop this driver, handing the chips to another
            if len(sel) > rules.minimum_drivers:
                for j in sel:
                    if j == i or current[j] + current[i] > rules.max_chips_per_driver:
                        continue
                    cand = current.copy()
                    cand[j] += cand[i]
                    cand[i] = 0
                    if _valid(cand, teams, rules):
                        v = value(cand)
                        if v > best_val:
                            best_move, best_val = cand, v
        if best_move is None:
            break
        current, current_val = best_move, best_val
    return current


def pick_eleventh(sim: SimulationResult, top: int = 3) -> list[tuple[str, float]]:
    """Drivers most likely to be classified exactly 11th, best first."""
    order = np.argsort(-sim.p11)[:top]
    return [(sim.driver_ids[i], float(sim.p11[i])) for i in order]


def optimize_allocation(
    sim: SimulationResult,
    team_of: dict[str, str],
    rules: PoolRules = DEFAULT_RULES,
    risk_aversion: float = 0.0,
) -> AllocationResult:
    """Best allocation for a simulated session under the pool's rules."""
    teams = [team_of.get(d) or None for d in sim.driver_ids]
    candidates = pick_eleventh(sim)
    pick, pick_p = (candidates[0] if candidates else (None, 0.0))
    bonus_ev = pick_p * rules.eleventh_bonus * rules.multiplier(sim.sprint)

    start = _ev_optimum(sim, team_of, rules)
    if start is None:
        distinct = len({t for t in teams if t})
        return AllocationResult(
            allocation={}, expected_score=0.0, std=0.0, p10=0.0, p50=0.0, p90=0.0,
            eleventh_pick=pick, eleventh_probability=pick_p,
            eleventh_candidates=candidates, expected_total=bonus_ev,
            risk_aversion=risk_aversion,
            errors=[
                f"No valid allocation: the rules need {rules.minimum_drivers} drivers "
                f"from different teams and only {distinct} team(s) are available."
            ],
        )

    chips = np.zeros(len(teams), dtype=np.int64)
    for i, c in start.items():
        chips[i] = c
    if risk_aversion != 0.0:
        chips = _local_search(sim, chips, teams, rules, risk_aversion)

    scores = _portfolio(sim, chips, rules)
    allocation = {sim.driver_ids[i]: int(chips[i]) for i in np.flatnonzero(chips)}
    allocation = dict(sorted(allocation.items(), key=lambda kv: -kv[1]))
    return AllocationResult(
        allocation=allocation,
        expected_score=float(scores.mean()),
        std=float(scores.std()),
        p10=float(np.percentile(scores, 10)),
        p50=float(np.percentile(scores, 50)),
        p90=float(np.percentile(scores, 90)),
        eleventh_pick=pick,
        eleventh_probability=pick_p,
        eleventh_candidates=candidates,
        expected_total=float(scores.mean()) + bonus_ev,
        risk_aversion=risk_aversion,
        errors=rules.validate(allocation, team_of),
    )
