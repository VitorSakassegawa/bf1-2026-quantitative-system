"""Derived / composite KPIs combining driver, team, and circuit data."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from loguru import logger


class DerivedKPICalculator:
    """Cross-cutting KPIs that combine multiple data sources."""

    def calculate_momentum(
        self,
        recent_points: list[float],
        expected_points: list[float],
    ) -> float:
        """
        Momentum over the last N races.
        momentum = sum(actual) / sum(expected)
        > 1.0 = over-performing, < 1.0 = under-performing
        """
        total_actual = sum(recent_points)
        total_expected = sum(expected_points)
        if total_expected == 0:
            return 1.0
        return float(total_actual / total_expected)

    def calculate_driver_volatility(
        self, positions: list[int | float]
    ) -> float:
        """
        Driver volatility (current season).
        volatility = std(positions) / mean(positions)
        Normalized to [0, 1].
        """
        if not positions or len(positions) < 2:
            return 0.5
        arr = np.array(positions, dtype=float)
        mean_pos = arr.mean()
        if mean_pos == 0:
            return 0.5
        raw = arr.std() / mean_pos
        return float(max(0.0, min(1.0, raw)))

    def calculate_risk_index(
        self,
        dnf_probability: float,
        weather_risk: float,
        circuit_risk: float,
    ) -> float:
        """
        Composite Risk Index.
        risk = 0.40 * dnf_prob + 0.35 * weather_risk + 0.25 * circuit_risk
        """
        risk = (
            0.40 * dnf_probability
            + 0.35 * weather_risk
            + 0.25 * circuit_risk
        )
        return float(max(0.0, min(1.0, risk)))

    def calculate_opportunity_index(
        self,
        avg_positions_gained: float,
        overtaking_index: float,
        grid_position: int,
    ) -> float:
        """
        Opportunity Index – potential for position gains given grid slot.
        opportunity = (avg_positions_gained * overtaking_index) / log(grid + 1)
        """
        if grid_position < 1:
            grid_position = 1
        denominator = math.log(grid_position + 1)
        if denominator == 0:
            return 0.0
        raw = (avg_positions_gained * overtaking_index) / denominator
        return float(raw)

    def calculate_form_trend(
        self, positions: list[int | float], window: int = 5
    ) -> float:
        """
        Linear regression slope over recent positions.
        Negative slope = improving form.
        """
        if len(positions) < 2:
            return 0.0
        recent = positions[-window:]
        x = np.arange(len(recent))
        y = np.array(recent, dtype=float)
        if len(x) < 2:
            return 0.0
        slope = np.polyfit(x, y, 1)[0]
        return float(-slope)  # negate so positive = improving

    def calculate_all(
        self,
        recent_points: list[float] | None = None,
        expected_points: list[float] | None = None,
        positions: list[int | float] | None = None,
        dnf_probability: float = 0.05,
        weather_risk: float = 0.1,
        circuit_risk: float = 0.1,
        avg_positions_gained: float = 0.0,
        overtaking_index: float = 0.5,
        grid_position: int = 10,
    ) -> dict:
        """Calculate all derived KPIs."""
        if recent_points is None:
            recent_points = []
        if expected_points is None:
            expected_points = []
        if positions is None:
            positions = []

        kpis = {
            "momentum": self.calculate_momentum(recent_points, expected_points),
            "volatility": self.calculate_driver_volatility(positions),
            "risk_index": self.calculate_risk_index(
                dnf_probability, weather_risk, circuit_risk
            ),
            "opportunity_index": self.calculate_opportunity_index(
                avg_positions_gained, overtaking_index, grid_position
            ),
            "form_trend": self.calculate_form_trend(positions),
        }

        logger.debug(
            f"Derived KPIs: momentum={kpis['momentum']:.2f}, "
            f"risk={kpis['risk_index']:.2f}"
        )
        return kpis
