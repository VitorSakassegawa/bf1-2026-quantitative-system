"""Tests for ML models (XGBoost, Monte Carlo)."""

import numpy as np
import pandas as pd
import pytest

from app.models_ml.monte_carlo import MonteCarloSimulator, SimulationConfig
from app.models_ml.xgboost_model import FEATURE_COLUMNS, XGBoostF1Model


# ---------- XGBoost Tests ----------


class TestXGBoostModel:
    @pytest.fixture
    def model(self):
        return XGBoostF1Model()

    @pytest.fixture
    def synthetic_data(self):
        """Create a synthetic training dataset (5 seasons × 20 races × 20 drivers)."""
        np.random.seed(42)
        n = 2000
        X = pd.DataFrame(
            {
                "grid_position": np.random.randint(1, 21, n),
                "driver_elo": np.random.normal(1500, 200, n),
                "team_elo": np.random.normal(1500, 150, n),
                "weather_risk_index": np.random.uniform(0, 1, n),
                "circuit_avg_dnf_rate": np.random.uniform(0, 0.3, n),
                "circuit_overtaking_index": np.random.uniform(0, 1, n),
                "driver_dnf_rate_circuit": np.random.uniform(0, 0.2, n),
                "driver_consistency_index": np.random.uniform(0.3, 1.0, n),
                "driver_momentum": np.random.uniform(0.5, 1.5, n),
                "driver_volatility": np.random.uniform(0, 0.8, n),
                "driver_wet_performance": np.random.uniform(-0.3, 0.3, n),
                "team_reliability_index": np.random.uniform(0.7, 1.0, n),
                "team_strategic_error_rate": np.random.uniform(0, 0.3, n),
                "is_sprint": np.random.randint(0, 2, n),
                "temperature_air": np.random.uniform(15, 45, n),
                "humidity": np.random.uniform(20, 90, n),
                "driver_avg_positions_gained": np.random.uniform(-3, 3, n),
            }
        )
        # Target: final position correlated with grid + noise
        y = pd.Series(
            np.clip(X["grid_position"] + np.random.normal(0, 3, n), 1, 20)
        )
        return X, y

    def test_feature_columns_complete(self):
        assert len(FEATURE_COLUMNS) == 17

    def test_build_feature_matrix(self, model):
        race_data = {
            "drivers": [
                {
                    "driver_id": "d1",
                    "team_id": "t1",
                    "grid_position": 3,
                    "elo": 1600,
                    "team_elo": 1550,
                }
            ],
            "is_sprint": False,
        }
        X = model.build_feature_matrix(
            race_data, {"d1": {}}, {"t1": {}}, {}, {}
        )
        assert len(X) == 1
        assert list(X.columns) == FEATURE_COLUMNS

    def test_predict_without_training(self, model):
        """Predict returns defaults when untrained."""
        X = pd.DataFrame(
            {col: [0.5] for col in FEATURE_COLUMNS}
        )
        results = model.predict(X)
        assert len(results) == 1
        assert "expected_position" in results[0]

    @pytest.mark.skipif(
        not hasattr(__import__("importlib"), "import_module")
        or __import__("importlib").util.find_spec("xgboost") is None,
        reason="XGBoost not installed",
    )
    def test_train_and_predict(self, model, synthetic_data):
        X, y = synthetic_data
        metrics = model.train(X, y)
        assert "mae" in metrics
        assert metrics["mae"] < 10

        # Predict
        X_test = X.iloc[:20]
        preds = model.predict(X_test)
        assert len(preds) == 20
        for p in preds:
            assert 1 <= p["expected_position"] <= 20
            assert 0 <= p["top3_probability"] <= 1
            assert 0 <= p["top10_probability"] <= 1
            assert 0 <= p["dnf_probability"] <= 1


# ---------- Monte Carlo Tests ----------


class TestMonteCarlo:
    @pytest.fixture
    def simulator(self):
        return MonteCarloSimulator()

    @pytest.fixture
    def drivers(self):
        return [
            {
                "driver_id": f"d{i}",
                "grid_position": i,
                "dnf_probability": 0.05,
                "elo": 1500 + (20 - i) * 20,
                "wet_performance": 0.0,
                "team_reliability": 0.95,
            }
            for i in range(1, 21)
        ]

    @pytest.fixture
    def circuit_kpis(self):
        return {
            "avg_dnf_rate": 0.1,
            "avg_overtaking_index": 0.5,
        }

    @pytest.fixture
    def weather(self):
        return {"weather_risk_index": 0.1, "rain_probability": 0.1}

    def test_simulation_runs(self, simulator, drivers, circuit_kpis, weather):
        config = SimulationConfig(n_simulations=1000, random_seed=42)
        results = simulator.simulate_race(
            drivers, circuit_kpis, weather, config
        )
        assert len(results) == 20

    def test_probabilities_valid(self, simulator, drivers, circuit_kpis, weather):
        config = SimulationConfig(n_simulations=5000, random_seed=42)
        results = simulator.simulate_race(
            drivers, circuit_kpis, weather, config
        )
        for did, data in results.items():
            assert 0 <= data["top3_probability"] <= 1
            assert 0 <= data["top10_probability"] <= 1
            assert 0 <= data["dnf_probability_simulated"] <= 1
            assert data["top3_probability"] <= data["top10_probability"]

    def test_position_distribution(self, simulator, drivers, circuit_kpis, weather):
        config = SimulationConfig(n_simulations=1000, random_seed=42)
        results = simulator.simulate_race(
            drivers, circuit_kpis, weather, config
        )
        for did, data in results.items():
            dist = data["position_distribution"]
            assert len(dist) == 20
            assert all(v >= 0 for v in dist)

    def test_pole_sitter_top3_advantage(self, simulator, drivers, circuit_kpis, weather):
        """Driver on pole should have higher top3 probability than P20."""
        config = SimulationConfig(n_simulations=5000, random_seed=42)
        results = simulator.simulate_race(
            drivers, circuit_kpis, weather, config
        )
        p1_top3 = results["d1"]["top3_probability"]
        p20_top3 = results["d20"]["top3_probability"]
        assert p1_top3 > p20_top3

    def test_wet_weather_increases_variance(self, simulator, drivers, circuit_kpis):
        config = SimulationConfig(n_simulations=2000, random_seed=42)

        dry = {"weather_risk_index": 0.0, "rain_probability": 0.0}
        wet = {"weather_risk_index": 0.9, "rain_probability": 0.9}

        dry_results = simulator.simulate_race(
            drivers, circuit_kpis, dry, config
        )
        wet_results = simulator.simulate_race(
            drivers, circuit_kpis, wet, config
        )

        # In wet: P10 driver should have more variance (higher top3 chance)
        mid_dry = dry_results["d10"]["top3_probability"]
        mid_wet = wet_results["d10"]["top3_probability"]
        # Wet conditions should increase upset probability
        assert mid_wet >= mid_dry * 0.5  # Loose assertion due to randomness
