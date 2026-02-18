"""Team KPI calculations – constructor-level performance metrics."""

from __future__ import annotations

import pandas as pd
from loguru import logger


class TeamKPICalculator:
    """KPIs for F1 constructors / teams."""

    def calculate_qualifying_pace(self, qualifying_df: pd.DataFrame) -> float:
        """
        Average qualifying pace using gap_to_pole for both drivers.
        Lower = faster team.
        """
        if qualifying_df.empty:
            return 1.0
        if "gap_to_pole" in qualifying_df.columns:
            gaps = qualifying_df["gap_to_pole"].dropna()
            if not gaps.empty:
                return float(gaps.mean())
        if "grid_position" in qualifying_df.columns:
            return float(qualifying_df["grid_position"].mean())
        return 10.0

    def calculate_race_pace(self, results_df: pd.DataFrame) -> float:
        """Average finishing position of both team drivers."""
        if results_df.empty:
            return 10.0
        positions = results_df["final_position"].dropna()
        if positions.empty:
            return 10.0
        return float(positions.mean())

    def calculate_pit_stop_performance(self, pit_df: pd.DataFrame) -> dict:
        """
        Pit stop statistics:
        - avg_pit_time (seconds)
        - pit_time_std
        - fastest_pit
        """
        default = {"avg_pit_time": 2.5, "pit_time_std": 0.5, "fastest_pit": 2.0}
        if pit_df.empty or "duration" not in pit_df.columns:
            return default

        # Parse duration strings to float seconds
        durations = []
        for d in pit_df["duration"]:
            try:
                if isinstance(d, str):
                    parts = d.split(":")
                    if len(parts) == 2:
                        durations.append(int(parts[0]) * 60 + float(parts[1]))
                    else:
                        durations.append(float(d))
                elif isinstance(d, (int, float)):
                    durations.append(float(d))
            except (ValueError, TypeError):
                continue

        if not durations:
            return default

        import numpy as np

        arr = np.array(durations)
        return {
            "avg_pit_time": float(arr.mean()),
            "pit_time_std": float(arr.std()) if len(arr) > 1 else 0.0,
            "fastest_pit": float(arr.min()),
        }

    def calculate_tire_strategy_performance(
        self,
        results_df: pd.DataFrame,
        pit_df: pd.DataFrame,
    ) -> dict:
        """
        Measure tire strategy efficiency.
        Compare pit stops vs position outcome.
        """
        if results_df.empty or pit_df.empty:
            return {"strategy_efficiency": 0.5}

        if "pit_stops" not in results_df.columns:
            return {"strategy_efficiency": 0.5}

        df = results_df.dropna(subset=["final_position"])
        if df.empty:
            return {"strategy_efficiency": 0.5}

        gains = df["grid_position"] - df["final_position"]
        avg_gain = gains.mean()
        avg_pits = df["pit_stops"].mean()

        efficiency = avg_gain / (avg_pits + 1)
        normalized = max(0.0, min(1.0, (efficiency + 5) / 10.0))

        return {"strategy_efficiency": float(normalized)}

    def calculate_strategic_error_rate(self, results_df: pd.DataFrame) -> float:
        """
        Proxy: races where a driver lost 3+ positions after pit stop.
        error_rate = bad_races / total_races
        """
        if results_df.empty:
            return 0.1
        df = results_df.dropna(subset=["final_position"])
        if df.empty:
            return 0.1
        lost_positions = df["final_position"] - df["grid_position"]
        bad_races = (lost_positions >= 3).sum()
        return float(bad_races / len(df))

    def calculate_mechanical_reliability(self, results_df: pd.DataFrame) -> float:
        """
        1.0 - mechanical_dnf_rate
        Distinguishes mechanical DNFs from accidents where possible.
        """
        if results_df.empty:
            return 0.95
        if "dnf" not in results_df.columns:
            return 0.95

        total = len(results_df)
        if total == 0:
            return 0.95

        if "dnf_reason" in results_df.columns:
            mechanical_keywords = [
                "engine", "gearbox", "hydraulic", "electrical",
                "brake", "suspension", "power unit", "mechanical",
            ]
            mechanical_dnfs = 0
            for _, row in results_df[results_df["dnf"] == True].iterrows():
                reason = str(row.get("dnf_reason", "")).lower()
                if any(kw in reason for kw in mechanical_keywords):
                    mechanical_dnfs += 1
            return float(1.0 - mechanical_dnfs / total)

        dnf_rate = results_df["dnf"].sum() / total
        return float(1.0 - dnf_rate * 0.7)  # assume 70% of DNFs are mechanical

    def calculate_all(
        self,
        results_df: pd.DataFrame,
        qualifying_df: pd.DataFrame | None = None,
        pit_df: pd.DataFrame | None = None,
    ) -> dict:
        """Calculate all team KPIs."""
        if qualifying_df is None:
            qualifying_df = pd.DataFrame()
        if pit_df is None:
            pit_df = pd.DataFrame()

        kpis = {
            "qualifying_pace": self.calculate_qualifying_pace(qualifying_df),
            "race_pace": self.calculate_race_pace(results_df),
            "pit_stop_performance": self.calculate_pit_stop_performance(pit_df),
            "tire_strategy": self.calculate_tire_strategy_performance(
                results_df, pit_df
            ),
            "strategic_error_rate": self.calculate_strategic_error_rate(results_df),
            "mechanical_reliability": self.calculate_mechanical_reliability(results_df),
        }

        logger.debug(
            f"Team KPIs: pace={kpis['race_pace']:.1f}, "
            f"reliability={kpis['mechanical_reliability']:.2f}"
        )
        return kpis
