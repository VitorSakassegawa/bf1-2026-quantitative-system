"""Token optimizer – allocation of the 15 BF1 chips under the pool's rules."""

from __future__ import annotations

from loguru import logger

from app.models_ml.race_model import SimulationResult
from app.optimizer.ev_calculator import EVCalculator
from app.optimizer.pool_optimizer import (
    RISK_PROFILES,
    exact_ev_allocation,
    optimize_allocation,
)
from app.rules.pool_rules import DEFAULT_RULES, PoolRules


class TokenOptimizer:
    """
    Choose who gets the chips.

    Rules enforced (from the pool's own rule set):
    - 15 chips in total, at most 5 on one driver, at least 1 on each pick
    - at least 5 drivers, and never two from the same team

    The search is exact for expected value (see ``pool_optimizer``). The four
    strategy names are risk postures around that optimum:

    - ``balanced``          maximum expected score — the default, and the
                            right choice over a whole season
    - ``conservative``      gives up a little expectation for a tighter range
    - ``aggressive`` /
      ``ultra_aggressive``  prefer a wider range, for chasing a gap late on

    Risk postures need the full simulation; given only per-driver summaries
    every strategy falls back to the expected-value optimum.
    """

    def __init__(self, rules: PoolRules = DEFAULT_RULES) -> None:
        self.rules = rules
        self.ev_calc = EVCalculator(rules)

    # Kept as attributes because callers and tests read them.
    @property
    def TOTAL_TOKENS(self) -> int:  # noqa: N802
        return self.rules.total_chips

    @property
    def MAX_PER_DRIVER(self) -> int:  # noqa: N802
        return self.rules.max_chips_per_driver

    @property
    def MIN_DRIVERS(self) -> int:  # noqa: N802
        return self.rules.minimum_drivers

    MIN_TEAMS = DEFAULT_RULES.minimum_drivers

    def optimize(
        self,
        predictions: dict[str, dict],
        team_membership: dict[str, str],
        strategy_type: str = "balanced",
        is_sprint: bool = False,
        simulation: SimulationResult | None = None,
    ) -> dict:
        """Return the allocation and what to expect from it."""
        if not predictions and simulation is None:
            return {}

        risk = RISK_PROFILES.get(strategy_type, 0.0)
        extra: dict = {}

        if simulation is not None:
            result = optimize_allocation(simulation, team_membership, self.rules, risk)
            allocation, errors = result.allocation, result.errors
            total_ev = result.expected_score
            extra = {
                "std": round(result.std, 2),
                "p10": round(result.p10, 1),
                "p50": round(result.p50, 1),
                "p90": round(result.p90, 1),
                "eleventh_pick": result.eleventh_pick,
                "eleventh_probability": round(result.eleventh_probability, 4),
                "eleventh_candidates": result.eleventh_candidates,
                "expected_total": round(result.expected_total, 2),
            }
        else:
            ids = list(predictions)
            picks = exact_ev_allocation(
                ids,
                [float(predictions[d].get("expected_points", 0.0)) for d in ids],
                [EVCalculator._retire_probability(predictions[d]) for d in ids],
                team_membership,
                self.rules,
            )
            if picks is None:
                teams = {team_membership.get(d) for d in ids if team_membership.get(d)}
                allocation = {}
                errors = [
                    f"No valid allocation: the rules need {self.rules.minimum_drivers} "
                    f"drivers from different teams and only {len(teams)} team(s) "
                    "are available."
                ]
            else:
                allocation = {ids[i]: c for i, c in sorted(picks.items(), key=lambda kv: -kv[1])}
                errors = self.rules.validate(allocation, team_membership)
            total_ev = self.ev_calc.calculate_portfolio_ev(allocation, predictions, is_sprint)

        if errors:
            logger.error(
                f"Allocation violates BF1 rules: {errors}. Check that "
                "team_membership is populated and that enough drivers are active."
            )
        else:
            logger.info(
                f"Optimized allocation ({strategy_type}): EV={total_ev:.2f}, "
                f"drivers={len(allocation)}"
            )

        return {
            "allocation": allocation,
            "total_ev": round(total_ev, 4),
            "strategy_type": strategy_type,
            "risk_aversion": risk,
            "constraint_errors": errors,
            **extra,
        }

    def check_constraints(
        self, allocation: dict[str, int], team_membership: dict[str, str]
    ) -> list[str]:
        """Every rule the allocation breaks (empty when valid)."""
        return self.rules.validate(allocation, team_membership)
