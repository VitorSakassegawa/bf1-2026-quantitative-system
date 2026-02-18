"""Dynamic ELO rating system adapted for Formula 1."""

from __future__ import annotations

from loguru import logger


class ELOEngine:
    """
    Multi-competitor ELO system for F1.

    Key differences from classic ELO:
    - 20 simultaneous competitors
    - Result is relative position, not win/loss
    - Different K-factors for race, sprint, qualifying
    - DNF penalization
    """

    K_FACTOR_RACE = 32
    K_FACTOR_SPRINT = 16
    K_FACTOR_QUALIFYING = 8
    BASE_ELO = 1500.0

    def calculate_expected_score(self, elo_a: float, elo_b: float) -> float:
        """E_a = 1 / (1 + 10^((elo_b - elo_a) / 400))"""
        return 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / 400.0))

    def update_elo_race(
        self,
        driver_results: list[dict],
        current_elos: dict[str, float] | None = None,
    ) -> dict[str, dict]:
        """
        Update ELO ratings after a race.

        For each pair (i, j): if driver i finished ahead of j, i wins the duel.
        DNF is treated as P20 for ELO purposes.

        Args:
            driver_results: List of {driver_id, final_position, dnf}
            current_elos: Optional dict of {driver_id: current_elo}

        Returns:
            dict of {driver_id: {elo_before, elo_after, elo_delta}}
        """
        return self._update_elo(
            driver_results, self.K_FACTOR_RACE, current_elos
        )

    def update_elo_sprint(
        self,
        sprint_results: list[dict],
        current_elos: dict[str, float] | None = None,
    ) -> dict[str, dict]:
        """Same as race update but with K_FACTOR_SPRINT (half impact)."""
        return self._update_elo(
            sprint_results, self.K_FACTOR_SPRINT, current_elos
        )

    def update_elo_qualifying(
        self,
        qualifying_results: list[dict],
        current_elos: dict[str, float] | None = None,
    ) -> dict[str, dict]:
        """Qualifying ELO update with reduced K-factor."""
        return self._update_elo(
            qualifying_results, self.K_FACTOR_QUALIFYING, current_elos
        )

    def _update_elo(
        self,
        results: list[dict],
        k_factor: float,
        current_elos: dict[str, float] | None = None,
    ) -> dict[str, dict]:
        if not results:
            return {}

        if current_elos is None:
            current_elos = {}

        # Assign effective positions: DNF → P20
        effective = []
        for r in results:
            driver_id = r["driver_id"]
            dnf = r.get("dnf", False)
            pos = r.get("final_position") or r.get("grid_position", 20)
            if dnf or pos is None:
                pos = 20
            elo = current_elos.get(driver_id, self.BASE_ELO)
            effective.append(
                {"driver_id": driver_id, "position": pos, "elo": elo}
            )

        n = len(effective)
        elo_changes: dict[str, float] = {e["driver_id"]: 0.0 for e in effective}

        # Pairwise comparison
        for i in range(n):
            for j in range(i + 1, n):
                a = effective[i]
                b = effective[j]

                expected_a = self.calculate_expected_score(a["elo"], b["elo"])
                expected_b = 1.0 - expected_a

                if a["position"] < b["position"]:
                    actual_a, actual_b = 1.0, 0.0
                elif a["position"] > b["position"]:
                    actual_a, actual_b = 0.0, 1.0
                else:
                    actual_a, actual_b = 0.5, 0.5

                # Scale K by number of opponents
                k_scaled = k_factor / (n - 1)

                elo_changes[a["driver_id"]] += k_scaled * (actual_a - expected_a)
                elo_changes[b["driver_id"]] += k_scaled * (actual_b - expected_b)

        # Build result
        updates: dict[str, dict] = {}
        for e in effective:
            did = e["driver_id"]
            elo_before = e["elo"]
            delta = elo_changes[did]
            elo_after = elo_before + delta
            updates[did] = {
                "elo_before": elo_before,
                "elo_after": elo_after,
                "elo_delta": delta,
            }

        logger.info(f"ELO updated for {len(updates)} drivers (K={k_factor})")
        return updates

    def update_team_elo(
        self,
        team_results: dict[str, list[int]],
        current_team_elos: dict[str, float] | None = None,
    ) -> dict[str, dict]:
        """
        Update team ELO based on average position of both drivers.

        Args:
            team_results: {team_id: [driver1_pos, driver2_pos]}
            current_team_elos: {team_id: current_elo}
        """
        if current_team_elos is None:
            current_team_elos = {}

        team_avg: list[dict] = []
        for team_id, positions in team_results.items():
            avg_pos = sum(positions) / len(positions) if positions else 20
            team_avg.append(
                {
                    "driver_id": team_id,  # reuse driver field
                    "final_position": avg_pos,
                    "dnf": False,
                }
            )

        return self._update_elo(
            team_avg, self.K_FACTOR_RACE, current_team_elos
        )

    def get_driver_elo_history(
        self, elo_records: list[dict], last_n: int = 10
    ) -> list[dict]:
        """Return the last N ELO records for trend analysis."""
        sorted_records = sorted(
            elo_records, key=lambda x: x.get("created_at", "")
        )
        return sorted_records[-last_n:]
