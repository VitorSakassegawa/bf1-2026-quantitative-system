"""Expected Value calculator for BF1 token allocations."""

from __future__ import annotations

from app.utils.validators import BF1_SPRINT_MULTIPLIER


class EVCalculator:
    """Calculate expected values for driver–token allocations."""

    # Sourced from the rules module rather than re-declared, so the engine and
    # the spec cannot drift apart.
    SPRINT_MULTIPLIER = BF1_SPRINT_MULTIPLIER

    def calculate_driver_ev(
        self,
        prediction: dict,
        tokens: int,
        is_sprint: bool = False,
    ) -> float:
        """Expected Value for a single driver with N tokens.

            EV = tokens * expected_points * sprint_multiplier

        CONTRACT: `prediction["expected_points"]` is the *unconditional*
        expected BF1 points for the session — it already integrates over
        finishing positions and already carries BF1_DNF_PENALTY for the
        retirement mass. It is NOT pre-scaled by the sprint multiplier; that
        is applied here, in exactly one place.

        The previous formula multiplied that expectation by `top10_probability`
        and then subtracted `dnf_prob * 10` on top, double-charging retirements
        and re-applying a scoring probability already baked into the value. The
        error scaled with each driver's own probabilities, so it distorted the
        *ranking*, not just the magnitude: midfielders came out at ~0.25x their
        true EV and backmarkers went negative, which pushed the optimizer off
        the drivers the team-diversity rule forces it to pick.
        """
        expected_pts = prediction.get("expected_points", 0.0)
        sprint_mult = self.SPRINT_MULTIPLIER if is_sprint else 1.0

        ev = tokens * expected_pts * sprint_mult
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
