"""Expected Value calculator for BF1 token allocations."""

from __future__ import annotations

from app.utils.validators import BF1_DNF_PENALTY, BF1_POINTS_TABLE


class EVCalculator:
    """Calculate expected values for driver–token allocations."""

    SPRINT_MULTIPLIER = 2.0

    def calculate_driver_ev(
        self,
        prediction: dict,
        tokens: int,
        is_sprint: bool = False,
    ) -> float:
        """
        Expected Value for a single driver with N tokens.

        EV = tokens * (
            (top10_prob * expected_points * sprint_mult)
            - (dnf_prob * 10)
        )
        """
        top10_prob = prediction.get("top10_probability", 0.5)
        expected_pts = prediction.get("expected_points", 2.0)
        dnf_prob = prediction.get("dnf_probability", 0.05)

        sprint_mult = self.SPRINT_MULTIPLIER if is_sprint else 1.0

        ev = tokens * (
            (top10_prob * expected_pts * sprint_mult)
            - (dnf_prob * abs(BF1_DNF_PENALTY))
        )
        return round(ev, 4)

    def calculate_portfolio_ev(
        self,
        allocation: dict[str, int],
        predictions: dict[str, dict],
        is_sprint: bool = False,
    ) -> float:
        """Total portfolio EV = sum of individual driver EVs."""
        total = 0.0
        for driver_id, tokens in allocation.items():
            pred = predictions.get(driver_id, {})
            total += self.calculate_driver_ev(pred, tokens, is_sprint)
        return round(total, 4)

    def calculate_correlation_penalty(
        self,
        allocation: dict[str, int],
        team_membership: dict[str, str],
    ) -> float:
        """
        Penalization for correlated team bets.
        If 2 drivers from the same team have high tokens:
        penalty = tokens_p1 * tokens_p2 * 0.1
        """
        # Group drivers by team
        teams: dict[str, list[tuple[str, int]]] = {}
        for did, tokens in allocation.items():
            team = team_membership.get(did, "unknown")
            teams.setdefault(team, []).append((did, tokens))

        penalty = 0.0
        for team_id, members in teams.items():
            if len(members) >= 2:
                # All pairs
                for i in range(len(members)):
                    for j in range(i + 1, len(members)):
                        penalty += members[i][1] * members[j][1] * 0.1

        return round(penalty, 4)
