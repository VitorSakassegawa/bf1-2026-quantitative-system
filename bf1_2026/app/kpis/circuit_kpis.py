"""Circuit KPI calculations from historical race data."""

from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger


class CircuitKPICalculator:
    """
    Calculate all circuit-level KPIs from historical data.
    Uses pandas for vectorized aggregations.
    Default historical window: 5 years.
    """

    def __init__(self, window_years: int = 5) -> None:
        self.window_years = window_years

    def calculate_overtaking_index(self, race_results_df: pd.DataFrame) -> float:
        """
        Average number of positions gained/lost per driver per race.
        overtaking_index = mean(abs(final_position - grid_position)) / 20
        """
        if race_results_df.empty:
            return 0.0
        df = race_results_df.dropna(subset=["final_position", "grid_position"])
        if df.empty:
            return 0.0
        position_changes = (df["grid_position"] - df["final_position"]).abs()
        return float(position_changes.mean() / 20.0)

    def calculate_winner_grid_avg(self, race_results_df: pd.DataFrame) -> float:
        """
        Average grid position of the race winner over recent years.
        Low value = hard to overtake (Monaco ~1.0).
        High value = more upsets (Spa, Interlagos ~3.0+).
        """
        if race_results_df.empty:
            return 1.0
        winners = race_results_df[race_results_df["final_position"] == 1]
        if winners.empty:
            return 1.0
        return float(winners["grid_position"].mean())

    def calculate_safety_car_frequency(self, race_data_df: pd.DataFrame) -> float:
        """Average safety cars per race from historical data."""
        if race_data_df.empty or "safety_cars" not in race_data_df.columns:
            return 1.0
        return float(race_data_df["safety_cars"].mean())

    def calculate_dnf_rate(self, race_results_df: pd.DataFrame) -> float:
        """
        Average percentage of drivers who DNF.
        dnf_rate = DNFs / total_starters
        """
        if race_results_df.empty:
            return 0.0
        if "dnf" in race_results_df.columns:
            total = len(race_results_df)
            dnfs = race_results_df["dnf"].sum()
            return float(dnfs / total) if total > 0 else 0.0
        return 0.0

    def calculate_rain_frequency(self, weather_df: pd.DataFrame) -> float:
        """
        Historical frequency of rain during race sessions.
        rain_frequency = wet_races / total_races
        """
        if weather_df.empty:
            return 0.1
        if "rain_probability" in weather_df.columns:
            wet_races = (weather_df["rain_probability"] > 0.3).sum()
            return float(wet_races / len(weather_df))
        return 0.1

    def calculate_tire_degradation(self, race_results_df: pd.DataFrame) -> float:
        """
        Proxy for tire degradation: normalized average pit stops.
        degradation = (avg_pits - 1) / 3, clamped to [0.0, 1.0]
        """
        if race_results_df.empty or "pit_stops" not in race_results_df.columns:
            return 0.3
        avg_pits = race_results_df["pit_stops"].mean()
        return float(max(0.0, min((avg_pits - 1) / 3.0, 1.0)))

    def calculate_temperature_sensitivity(
        self,
        weather_df: pd.DataFrame,
        performance_df: pd.DataFrame,
    ) -> float:
        """
        Pearson correlation between track temperature and position variance.
        High correlation → temperature matters more at this circuit.
        """
        if weather_df.empty or performance_df.empty:
            return 0.0

        if (
            "temperature_track_estimated" not in weather_df.columns
            or "position_variance" not in performance_df.columns
        ):
            return 0.0

        merged = pd.merge(
            weather_df[["race_id", "temperature_track_estimated"]],
            performance_df[["race_id", "position_variance"]],
            on="race_id",
            how="inner",
        )
        if len(merged) < 3:
            return 0.0

        corr = merged["temperature_track_estimated"].corr(
            merged["position_variance"]
        )
        return float(corr) if not np.isnan(corr) else 0.0

    def calculate_all(
        self,
        race_results_df: pd.DataFrame,
        weather_df: pd.DataFrame | None = None,
        performance_df: pd.DataFrame | None = None,
    ) -> dict:
        """Calculate all circuit KPIs and return as a dict."""
        if weather_df is None:
            weather_df = pd.DataFrame()
        if performance_df is None:
            performance_df = pd.DataFrame()

        kpis = {
            "avg_overtaking_index": self.calculate_overtaking_index(race_results_df),
            "avg_winner_grid_position": self.calculate_winner_grid_avg(race_results_df),
            "avg_safety_cars": self.calculate_safety_car_frequency(
                race_results_df
            ),
            "avg_dnf_rate": self.calculate_dnf_rate(race_results_df),
            "historical_rain_frequency": self.calculate_rain_frequency(weather_df),
            "avg_tire_degradation": self.calculate_tire_degradation(race_results_df),
            "temperature_sensitivity": self.calculate_temperature_sensitivity(
                weather_df, performance_df
            ),
        }

        logger.info(f"Circuit KPIs calculated: {kpis}")
        return kpis
