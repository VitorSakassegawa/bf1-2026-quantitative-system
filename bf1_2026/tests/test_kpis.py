"""Tests for KPI calculators."""

import numpy as np
import pandas as pd
import pytest

from app.kpis.circuit_kpis import CircuitKPICalculator
from app.kpis.derived_kpis import DerivedKPICalculator
from app.kpis.driver_kpis import DriverKPICalculator
from app.kpis.team_kpis import TeamKPICalculator


# ---------- Fixtures ----------


@pytest.fixture
def circuit_calc():
    return CircuitKPICalculator()


@pytest.fixture
def driver_calc():
    return DriverKPICalculator()


@pytest.fixture
def team_calc():
    return TeamKPICalculator()


@pytest.fixture
def derived_calc():
    return DerivedKPICalculator()


@pytest.fixture
def sample_race_results():
    """Synthetic race results for 20 drivers, 5 races."""
    rows = []
    for race_id in range(1, 6):
        for pos in range(1, 21):
            dnf = pos >= 18 and race_id % 3 == 0
            rows.append(
                {
                    "race_id": race_id,
                    "driver_id": f"driver_{pos}",
                    "final_position": None if dnf else pos,
                    "grid_position": pos,
                    "dnf": dnf,
                    "pit_stops": 2 if pos <= 10 else 1,
                }
            )
    return pd.DataFrame(rows)


@pytest.fixture
def sample_weather():
    return pd.DataFrame(
        [
            {"race_id": 1, "rain_probability": 0.0, "temperature_track_estimated": 40.0},
            {"race_id": 2, "rain_probability": 0.8, "temperature_track_estimated": 22.0},
            {"race_id": 3, "rain_probability": 0.1, "temperature_track_estimated": 50.0},
            {"race_id": 4, "rain_probability": 0.5, "temperature_track_estimated": 30.0},
            {"race_id": 5, "rain_probability": 0.0, "temperature_track_estimated": 45.0},
        ]
    )


# ---------- Circuit KPI Tests ----------


class TestCircuitKPIs:
    def test_overtaking_index_no_changes(self, circuit_calc):
        """Grid == result → overtaking_index = 0."""
        df = pd.DataFrame(
            {"final_position": [1, 2, 3], "grid_position": [1, 2, 3]}
        )
        assert circuit_calc.calculate_overtaking_index(df) == 0.0

    def test_overtaking_index_with_changes(self, circuit_calc):
        df = pd.DataFrame(
            {"final_position": [3, 1, 2], "grid_position": [1, 2, 3]}
        )
        idx = circuit_calc.calculate_overtaking_index(df)
        assert idx > 0.0

    def test_dnf_rate_no_dnfs(self, circuit_calc):
        df = pd.DataFrame({"dnf": [False, False, False]})
        assert circuit_calc.calculate_dnf_rate(df) == 0.0

    def test_dnf_rate_all_dnfs(self, circuit_calc):
        df = pd.DataFrame({"dnf": [True, True, True]})
        assert circuit_calc.calculate_dnf_rate(df) == 1.0

    def test_tire_degradation_clamped(self, circuit_calc):
        df = pd.DataFrame({"pit_stops": [5, 6, 7]})
        result = circuit_calc.calculate_tire_degradation(df)
        assert 0.0 <= result <= 1.0

    def test_calculate_all_returns_dict(self, circuit_calc, sample_race_results):
        kpis = circuit_calc.calculate_all(sample_race_results)
        assert isinstance(kpis, dict)
        assert "avg_overtaking_index" in kpis
        assert "avg_dnf_rate" in kpis

    def test_empty_dataframe(self, circuit_calc):
        df = pd.DataFrame()
        assert circuit_calc.calculate_dnf_rate(df) == 0.0
        assert circuit_calc.calculate_overtaking_index(df) == 0.0


# ---------- Driver KPI Tests ----------


class TestDriverKPIs:
    def test_avg_finish_position(self, driver_calc):
        df = pd.DataFrame({"final_position": [1, 2, 3]})
        assert driver_calc.calculate_avg_finish_position(df) == 2.0

    def test_avg_finish_with_dnf(self, driver_calc):
        """DNFs (NaN) treated as P21."""
        df = pd.DataFrame({"final_position": [1, None, 3]})
        result = driver_calc.calculate_avg_finish_position(df)
        assert result == pytest.approx((1 + 21 + 3) / 3, rel=0.01)

    def test_positions_gained(self, driver_calc):
        df = pd.DataFrame(
            {"grid_position": [5, 10, 3], "final_position": [2, 8, 1]}
        )
        gain = driver_calc.calculate_avg_positions_gained(df)
        assert gain > 0  # gained positions on average

    def test_consistency_index_range(self, driver_calc):
        df = pd.DataFrame({"final_position": [3, 4, 5, 3, 4]})
        ci = driver_calc.calculate_consistency_index(df)
        assert 0.0 <= ci <= 1.0

    def test_consistency_high_for_consistent(self, driver_calc):
        consistent = pd.DataFrame({"final_position": [3, 3, 3, 3]})
        inconsistent = pd.DataFrame({"final_position": [1, 20, 2, 19]})
        ci_high = driver_calc.calculate_consistency_index(consistent)
        ci_low = driver_calc.calculate_consistency_index(inconsistent)
        assert ci_high > ci_low


# ---------- Team KPI Tests ----------


class TestTeamKPIs:
    def test_reliability_no_dnf(self, team_calc):
        df = pd.DataFrame({"dnf": [False, False, False]})
        rel = team_calc.calculate_mechanical_reliability(df)
        assert rel == 1.0

    def test_strategic_error_rate(self, team_calc):
        df = pd.DataFrame(
            {
                "final_position": [10, 2, 15],
                "grid_position": [3, 1, 5],
            }
        )
        rate = team_calc.calculate_strategic_error_rate(df)
        assert 0.0 <= rate <= 1.0


# ---------- Derived KPI Tests ----------


class TestDerivedKPIs:
    def test_momentum_equal(self, derived_calc):
        m = derived_calc.calculate_momentum([10, 10], [10, 10])
        assert m == pytest.approx(1.0)

    def test_momentum_over_performing(self, derived_calc):
        m = derived_calc.calculate_momentum([20, 20], [10, 10])
        assert m > 1.0

    def test_risk_index_range(self, derived_calc):
        r = derived_calc.calculate_risk_index(0.5, 0.5, 0.5)
        assert 0.0 <= r <= 1.0

    def test_risk_index_zero(self, derived_calc):
        r = derived_calc.calculate_risk_index(0.0, 0.0, 0.0)
        assert r == 0.0

    def test_opportunity_index(self, derived_calc):
        opp = derived_calc.calculate_opportunity_index(2.0, 0.8, 10)
        assert opp > 0

    def test_volatility_range(self, derived_calc):
        v = derived_calc.calculate_driver_volatility([1, 5, 10, 15, 20])
        assert 0.0 <= v <= 1.0
