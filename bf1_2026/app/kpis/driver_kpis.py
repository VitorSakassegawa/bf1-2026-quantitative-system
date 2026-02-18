"""Driver KPI calculations – per-driver, per-circuit metrics."""

from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger


class DriverKPICalculator:
    """KPIs for a specific driver at a specific circuit (last 5 years)."""

    def __init__(self, window_years: int = 5) -> None:
        self.window_years = window_years

    def calculate_avg_finish_position(self, results_df: pd.DataFrame) -> float:
        """Average final position (DNFs treated as P21)."""
        if results_df.empty:
            return 15.0
        positions = results_df["final_position"].copy()
        positions = positions.fillna(21)
        return float(positions.mean())

    def calculate_avg_grid_position(self, results_df: pd.DataFrame) -> float:
        """Average qualifying / grid position."""
        if results_df.empty:
            return 10.0
        return float(results_df["grid_position"].mean())

    def calculate_avg_positions_gained(self, results_df: pd.DataFrame) -> float:
        """
        Average positions gained per race.
        gain = grid_position - final_position (positive = gained places)
        """
        if results_df.empty:
            return 0.0
        df = results_df.dropna(subset=["final_position"])
        if df.empty:
            return 0.0
        gains = df["grid_position"] - df["final_position"]
        return float(gains.mean())

    def calculate_performance_std(self, results_df: pd.DataFrame) -> float:
        """
        Standard deviation of finishing position (normalized by 20 drivers).
        High std = inconsistent at this circuit.
        """
        if results_df.empty:
            return 0.5
        positions = results_df["final_position"].dropna()
        if len(positions) < 2:
            return 0.5
        return float(positions.std() / 20.0)

    def calculate_dnf_rate(self, results_df: pd.DataFrame) -> float:
        """DNF rate at this specific circuit."""
        if results_df.empty:
            return 0.0
        if "dnf" not in results_df.columns:
            return 0.0
        return float(results_df["dnf"].mean())

    def calculate_wet_performance(
        self,
        results_df: pd.DataFrame,
        weather_df: pd.DataFrame,
    ) -> float:
        """
        Wet weather performance index.
        wet_index = (avg_dry_position - avg_wet_position) / avg_dry_position
        Positive = better in rain.
        """
        if results_df.empty or weather_df.empty:
            return 0.0

        merged = pd.merge(
            results_df, weather_df[["race_id", "rain_probability"]], on="race_id", how="left"
        )
        merged["rain_probability"] = merged["rain_probability"].fillna(0.0)

        wet = merged[merged["rain_probability"] > 0.3]
        dry = merged[merged["rain_probability"] <= 0.3]

        wet_positions = wet["final_position"].dropna()
        dry_positions = dry["final_position"].dropna()

        if wet_positions.empty or dry_positions.empty:
            return 0.0

        avg_dry = dry_positions.mean()
        avg_wet = wet_positions.mean()

        if avg_dry == 0:
            return 0.0
        return float((avg_dry - avg_wet) / avg_dry)

    def calculate_sprint_performance(self, sprint_df: pd.DataFrame) -> float:
        """Average finishing position in sprint races (global)."""
        if sprint_df.empty:
            return 10.0
        positions = sprint_df["final_position"].dropna()
        if positions.empty:
            return 10.0
        return float(positions.mean())

    def calculate_consistency_index(self, results_df: pd.DataFrame) -> float:
        """
        Coefficient of variation inverted to a 0-1 scale.
        consistency = 1 / (1 + CV)
        where CV = std / mean
        """
        if results_df.empty:
            return 0.5
        positions = results_df["final_position"].dropna()
        if len(positions) < 2 or positions.mean() == 0:
            return 0.5
        cv = positions.std() / positions.mean()
        return float(1.0 / (1.0 + cv))

    def calculate_temperature_performance(
        self,
        results_df: pd.DataFrame,
        weather_df: pd.DataFrame,
    ) -> dict:
        """
        Segment performance by track temperature bands:
        - Cold: < 25°C
        - Medium: 25°C - 45°C
        - Hot: > 45°C
        """
        default = {"cold": None, "medium": None, "hot": None}
        if results_df.empty or weather_df.empty:
            return default

        merged = pd.merge(
            results_df,
            weather_df[["race_id", "temperature_track_estimated"]],
            on="race_id",
            how="left",
        )
        merged = merged.dropna(subset=["temperature_track_estimated", "final_position"])

        if merged.empty:
            return default

        cold = merged[merged["temperature_track_estimated"] < 25]
        medium = merged[
            (merged["temperature_track_estimated"] >= 25)
            & (merged["temperature_track_estimated"] <= 45)
        ]
        hot = merged[merged["temperature_track_estimated"] > 45]

        return {
            "cold": float(cold["final_position"].mean()) if not cold.empty else None,
            "medium": float(medium["final_position"].mean()) if not medium.empty else None,
            "hot": float(hot["final_position"].mean()) if not hot.empty else None,
        }

    def calculate_all(
        self,
        results_df: pd.DataFrame,
        weather_df: pd.DataFrame | None = None,
        sprint_df: pd.DataFrame | None = None,
    ) -> dict:
        """Calculate all driver KPIs and return as a dict."""
        if weather_df is None:
            weather_df = pd.DataFrame()
        if sprint_df is None:
            sprint_df = pd.DataFrame()

        kpis = {
            "avg_finish_position": self.calculate_avg_finish_position(results_df),
            "avg_grid_position": self.calculate_avg_grid_position(results_df),
            "avg_positions_gained": self.calculate_avg_positions_gained(results_df),
            "performance_std": self.calculate_performance_std(results_df),
            "dnf_rate": self.calculate_dnf_rate(results_df),
            "wet_performance": self.calculate_wet_performance(results_df, weather_df),
            "sprint_performance": self.calculate_sprint_performance(sprint_df),
            "consistency_index": self.calculate_consistency_index(results_df),
            "temperature_performance": self.calculate_temperature_performance(
                results_df, weather_df
            ),
        }

        logger.debug(f"Driver KPIs: avg_pos={kpis['avg_finish_position']:.1f}, "
                     f"consistency={kpis['consistency_index']:.2f}")
        return kpis
