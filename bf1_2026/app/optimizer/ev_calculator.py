"""Expected Value calculator for BF1 token allocations."""

from __future__ import annotations

from app.rules.pool_rules import DEFAULT_RULES, PoolRules


class EVCalculator:
    """Expected value of chips on a driver, under the pool's formula.

        score = chips x driver points + 11th bonus - retirement penalties

    CONTRACT: ``prediction["expected_points"]`` is the expected *position
    points* for the session (zero when the driver is not classified). It does
    not include the retirement penalty or the sprint doubling — both are
    applied here.

    The retirement penalty is a **flat** amount per selected driver. Earlier
    versions folded it into the per-chip expectation, which charged a 5-chip
    pick five times over; that is why the old optimiser spread chips thin and
    scored ~700 points worse over the 2026 season than its own most
    concentrated profile.
    """

    def __init__(self, rules: PoolRules = DEFAULT_RULES) -> None:
        self.rules = rules

    @property
    def SPRINT_MULTIPLIER(self) -> float:  # noqa: N802 (kept for compatibility)
        return self.rules.multiplier(sprint=True)

    @staticmethod
    def _retire_probability(prediction: dict) -> float:
        if "retire_probability" in prediction:
            return float(prediction["retire_probability"])
        return float(prediction.get("dnf_probability", 0.0))

    def calculate_driver_ev(
        self,
        prediction: dict,
        tokens: int,
        is_sprint: bool = False,
    ) -> float:
        """EV of ``tokens`` chips on one driver (0 when the driver is not picked)."""
        if tokens <= 0:
            return 0.0
        points = float(prediction.get("expected_points", 0.0))
        ev = tokens * points - self.rules.retirement_penalty * self._retire_probability(
            prediction
        )
        return round(ev * self.rules.multiplier(is_sprint), 4)

    def calculate_portfolio_ev(
        self,
        allocation: dict[str, int],
        predictions: dict[str, dict],
        is_sprint: bool = False,
    ) -> float:
        """Expected chip score of a whole bet (11th-place bonus not included)."""
        total = sum(
            self.calculate_driver_ev(predictions.get(driver_id, {}), tokens, is_sprint)
            for driver_id, tokens in allocation.items()
        )
        return round(total, 4)
