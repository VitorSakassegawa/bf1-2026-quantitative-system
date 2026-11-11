"""Monte Carlo race simulator — dict-based façade over ``race_model``.

The simulation itself lives in :mod:`app.models_ml.race_model`, which is
calibrated on real results and fully vectorised. This class keeps the
dict-in / dict-out interface the rest of the app (and its tests) grew up with.

What changed underneath, and why it matters to callers:

* ``expected_bf1_points`` is now **position points only**. The retirement
  penalty is no longer folded in, because the pool charges it once per driver
  rather than once per chip — it cannot be expressed as "points per chip".
  Use :meth:`calculate_expected_value`, which applies it correctly.
* The simulation is ~100x faster (20,000 runs of a 22-car grid in tens of
  milliseconds instead of seconds of Python-level looping).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from loguru import logger

from app.models_ml.race_model import (
    DEFAULT_PARAMS,
    ELO_BASELINE,
    Entry,
    ModelParams,
    SimulationResult,
    simulate,
)
from app.rules.pool_rules import DEFAULT_RULES, PoolRules

__all__ = ["ELO_BASELINE", "MonteCarloSimulator", "SimulationConfig"]


@dataclass
class SimulationConfig:
    n_simulations: int = 20000
    random_seed: int = 42
    params: ModelParams = field(default_factory=lambda: DEFAULT_PARAMS)


class MonteCarloSimulator:
    """Simulate a session and summarise each driver's prospects."""

    def __init__(self, rules: PoolRules = DEFAULT_RULES) -> None:
        self.rules = rules
        self.last_simulation: SimulationResult | None = None

    def simulate_race(
        self,
        drivers: list[dict],
        circuit_kpis: dict | None = None,
        weather: dict | None = None,
        config: SimulationConfig | None = None,
        is_sprint: bool = False,
    ) -> dict:
        """
        Run the simulation.

        Args:
            drivers: [{driver_id, grid_position, elo, team_id?, form?,
                       dnf_probability?}] — ``grid_position`` may be None when
                       qualifying has not happened yet.
            circuit_kpis: unused; kept for call compatibility.
            weather: {"weather_risk_index": 0..1} widens the outcome spread.
            config: simulation size, seed and model parameters.
            is_sprint: True when the session being simulated is the sprint.

        Returns:
            {driver_id: {expected_position, top3_probability,
                         top10_probability, eleventh_probability,
                         dnf_probability, retire_probability,
                         expected_points, position_distribution, ...}}
        """
        config = config or SimulationConfig()
        weather = weather or {}
        started = time.time()

        entries = [
            Entry(
                driver_id=d["driver_id"],
                team_id=str(d.get("team_id") or ""),
                grid=d.get("grid_position"),
                elo=d.get("elo", ELO_BASELINE),
                form=d.get("form"),
                dnf_probability=d.get("dnf_probability"),
            )
            for d in drivers
        ]
        sim = simulate(
            entries,
            sprint=is_sprint,
            rules=self.rules,
            params=config.params,
            n_simulations=config.n_simulations,
            seed=config.random_seed,
            weather_risk=float(weather.get("weather_risk_index", 0.0) or 0.0),
        )
        self.last_simulation = sim

        results = sim.summary()
        for did, r in results.items():
            r["expected_value"] = self.calculate_expected_value(r, 1, is_sprint)

        logger.info(
            f"Monte Carlo: {sim.n_simulations} sims, {len(drivers)} drivers "
            f"in {time.time() - started:.3f}s"
        )
        return results

    def calculate_expected_value(
        self,
        simulation_results: dict,
        token_allocation: int,
        is_sprint: bool = False,
    ) -> float:
        """EV of putting ``token_allocation`` chips on one driver.

            (chips x E[position points] - penalty x P(retire)) x sprint factor

        The penalty term does not scale with chips: that is the pool's rule.
        """
        if token_allocation <= 0:
            return 0.0
        pts = simulation_results.get(
            "expected_points", simulation_results.get("expected_bf1_points", 0.0)
        )
        p_retire = simulation_results.get(
            "retire_probability",
            simulation_results.get(
                "dnf_probability", simulation_results.get("dnf_probability_simulated", 0.0)
            ),
        )
        ev = token_allocation * pts - self.rules.retirement_penalty * p_retire
        return round(ev * self.rules.multiplier(is_sprint), 2)
