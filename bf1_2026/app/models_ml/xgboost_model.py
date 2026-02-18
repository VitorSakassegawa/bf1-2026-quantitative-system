"""XGBoost model for F1 position prediction and probabilities."""

from __future__ import annotations

import os
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger
from scipy import stats
from sklearn.metrics import mean_absolute_error, ndcg_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler

from app.config import settings
from app.models_ml.model_registry import ModelRegistry

try:
    import xgboost as xgb
except ImportError:
    xgb = None  # type: ignore[assignment]
    logger.warning("XGBoost not installed – model will not be available")


FEATURE_COLUMNS = [
    "grid_position",
    "driver_elo",
    "team_elo",
    "weather_risk_index",
    "circuit_avg_dnf_rate",
    "circuit_overtaking_index",
    "driver_dnf_rate_circuit",
    "driver_consistency_index",
    "driver_momentum",
    "driver_volatility",
    "driver_wet_performance",
    "team_reliability_index",
    "team_strategic_error_rate",
    "is_sprint",
    "temperature_air",
    "humidity",
    "driver_avg_positions_gained",
]

# Standard F1 points mapping
POINTS_TABLE = {
    1: 25, 2: 18, 3: 15, 4: 12, 5: 10,
    6: 8, 7: 6, 8: 4, 9: 2, 10: 1,
}


class XGBoostF1Model:
    """
    XGBoost model for predicting F1 finishing positions
    and computing probability estimates.

    Uses TimeSeriesSplit for validation (no future data leakage).
    """

    def __init__(self) -> None:
        self.model: xgb.XGBRegressor | None = None
        self.dnf_model: xgb.XGBClassifier | None = None
        self.scaler = StandardScaler()
        self.registry = ModelRegistry()
        self._version: str = "untrained"

    def build_feature_matrix(
        self,
        race_data: dict,
        driver_kpis: dict,
        team_kpis: dict,
        circuit_kpis: dict,
        weather: dict,
    ) -> pd.DataFrame:
        """Build the feature matrix for a set of drivers in a race."""
        rows = []
        drivers = race_data.get("drivers", [])

        for driver in drivers:
            did = driver.get("driver_id", "")
            d_kpi = driver_kpis.get(did, {})
            t_kpi = team_kpis.get(driver.get("team_id", ""), {})

            row = {
                "grid_position": driver.get("grid_position", 10),
                "driver_elo": driver.get("elo", 1500),
                "team_elo": driver.get("team_elo", 1500),
                "weather_risk_index": weather.get("weather_risk_index", 0.1),
                "circuit_avg_dnf_rate": circuit_kpis.get("avg_dnf_rate", 0.1),
                "circuit_overtaking_index": circuit_kpis.get(
                    "avg_overtaking_index", 0.5
                ),
                "driver_dnf_rate_circuit": d_kpi.get("dnf_rate", 0.05),
                "driver_consistency_index": d_kpi.get("consistency_index", 0.5),
                "driver_momentum": d_kpi.get("momentum", 1.0),
                "driver_volatility": d_kpi.get("volatility", 0.3),
                "driver_wet_performance": d_kpi.get("wet_performance", 0.0),
                "team_reliability_index": t_kpi.get("mechanical_reliability", 0.95),
                "team_strategic_error_rate": t_kpi.get(
                    "strategic_error_rate", 0.1
                ),
                "is_sprint": int(race_data.get("is_sprint", False)),
                "temperature_air": weather.get("temperature_air", 25),
                "humidity": weather.get("humidity", 50),
                "driver_avg_positions_gained": d_kpi.get(
                    "avg_positions_gained", 0.0
                ),
            }
            rows.append(row)

        return pd.DataFrame(rows, columns=FEATURE_COLUMNS)

    def train(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        incremental: bool = False,
    ) -> dict:
        """
        Train the position prediction model.

        Returns training metrics dict.
        """
        if xgb is None:
            raise RuntimeError("XGBoost is not installed")

        X_scaled = pd.DataFrame(
            self.scaler.fit_transform(X), columns=X.columns, index=X.index
        )

        params = {
            "objective": "reg:squarederror",
            "n_estimators": 500,
            "learning_rate": 0.05,
            "max_depth": 6,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "random_state": 42,
            "n_jobs": -1,
        }

        if incremental and self.model is not None:
            # Incremental update
            self.model.set_params(n_estimators=self.model.n_estimators + 100)
            self.model.fit(
                X_scaled,
                y,
                xgb_model=self.model.get_booster(),
                verbose=False,
            )
        else:
            tscv = TimeSeriesSplit(n_splits=5)
            best_score = float("inf")

            self.model = xgb.XGBRegressor(**params)

            for train_idx, val_idx in tscv.split(X_scaled):
                X_train, X_val = X_scaled.iloc[train_idx], X_scaled.iloc[val_idx]
                y_train, y_val = y.iloc[train_idx], y.iloc[val_idx]

                self.model.fit(
                    X_train,
                    y_train,
                    eval_set=[(X_val, y_val)],
                    verbose=False,
                )

                preds = self.model.predict(X_val)
                score = mean_absolute_error(y_val, preds)
                if score < best_score:
                    best_score = score

            # Final fit on all data
            self.model.fit(X_scaled, y, verbose=False)

        self._version = self.registry.generate_version()
        metrics = self.evaluate(X_scaled, y)
        logger.info(
            f"XGBoost trained v{self._version}: MAE={metrics['mae']:.3f}"
        )
        return metrics

    def train_dnf_classifier(
        self, X: pd.DataFrame, y_dnf: pd.Series
    ) -> dict:
        """Train a separate binary classifier for DNF probability."""
        if xgb is None:
            raise RuntimeError("XGBoost is not installed")

        X_scaled = pd.DataFrame(
            self.scaler.transform(X), columns=X.columns, index=X.index
        )

        self.dnf_model = xgb.XGBClassifier(
            objective="binary:logistic",
            n_estimators=300,
            learning_rate=0.05,
            max_depth=4,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            n_jobs=-1,
        )
        self.dnf_model.fit(X_scaled, y_dnf, verbose=False)

        preds = self.dnf_model.predict_proba(X_scaled)[:, 1]
        from sklearn.metrics import roc_auc_score

        try:
            auc = roc_auc_score(y_dnf, preds)
        except ValueError:
            auc = 0.5

        logger.info(f"DNF classifier trained: AUC={auc:.3f}")
        return {"auc": auc}

    def predict(self, X: pd.DataFrame) -> list[dict]:
        """
        Predict for each driver:
        - expected_position
        - top3_probability
        - top10_probability
        - dnf_probability
        - expected_points
        """
        if self.model is None:
            logger.warning("Model not trained – returning defaults")
            return [self._default_prediction() for _ in range(len(X))]

        X_scaled = pd.DataFrame(
            self.scaler.transform(X), columns=X.columns, index=X.index
        )

        positions = self.model.predict(X_scaled)

        # DNF probabilities
        if self.dnf_model is not None:
            dnf_probs = self.dnf_model.predict_proba(X_scaled)[:, 1]
        else:
            dnf_probs = np.full(len(X), 0.05)

        results = []
        for i in range(len(X)):
            pos = float(max(1, min(20, positions[i])))
            # Use volatility from features (if available) or default
            volatility = float(X.iloc[i].get("driver_volatility", 0.3))
            sigma = max(1.0, volatility * 10)

            dist = stats.norm(loc=pos, scale=sigma)
            top3_prob = float(dist.cdf(3.5))
            top10_prob = float(dist.cdf(10.5))
            dnf_prob = float(dnf_probs[i])

            # Expected BF1 points
            expected_pts = 0.0
            for p, pts in POINTS_TABLE.items():
                p_prob = dist.cdf(p + 0.5) - dist.cdf(p - 0.5)
                expected_pts += p_prob * pts
            expected_pts = expected_pts * (1 - dnf_prob) + dnf_prob * (-10)

            results.append(
                {
                    "expected_position": round(pos, 2),
                    "top3_probability": round(min(top3_prob, 1.0), 4),
                    "top10_probability": round(min(top10_prob, 1.0), 4),
                    "dnf_probability": round(min(dnf_prob, 1.0), 4),
                    "expected_points": round(expected_pts, 2),
                }
            )

        return results

    def save_model(self, path: str | None = None, version: str | None = None) -> str:
        """Save model + scaler + version to disk."""
        version = version or self._version
        storage = Path(path or settings.models_storage_path) / f"xgboost_{version}"
        storage.mkdir(parents=True, exist_ok=True)

        if self.model is not None:
            self.model.save_model(str(storage / "position_model.json"))
        if self.dnf_model is not None:
            self.dnf_model.save_model(str(storage / "dnf_model.json"))

        with open(storage / "scaler.pkl", "wb") as f:
            pickle.dump(self.scaler, f)

        self.registry.register(
            "xgboost_position", version, str(storage)
        )
        logger.info(f"Model saved to {storage}")
        return str(storage)

    def load_model(self, version: str = "latest") -> None:
        """Load model from the specified version."""
        if xgb is None:
            raise RuntimeError("XGBoost is not installed")

        if version == "latest":
            entry = self.registry.get_latest_version("xgboost_position")
        else:
            entry = self.registry.get_version("xgboost_position", version)

        if entry is None:
            logger.warning(f"No model found for version {version}")
            return

        storage = Path(entry["path"])

        pos_path = storage / "position_model.json"
        if pos_path.exists():
            self.model = xgb.XGBRegressor()
            self.model.load_model(str(pos_path))

        dnf_path = storage / "dnf_model.json"
        if dnf_path.exists():
            self.dnf_model = xgb.XGBClassifier()
            self.dnf_model.load_model(str(dnf_path))

        scaler_path = storage / "scaler.pkl"
        if scaler_path.exists():
            with open(scaler_path, "rb") as f:
                self.scaler = pickle.load(f)  # noqa: S301

        self._version = entry["version"]
        logger.info(f"Model loaded: v{self._version}")

    def evaluate(self, X: pd.DataFrame, y: pd.Series) -> dict:
        """Evaluate: MAE position, NDCG ranking, accuracy top3/top10."""
        if self.model is None:
            return {"mae": 99, "ndcg": 0, "top3_acc": 0, "top10_acc": 0}

        preds = self.model.predict(X)
        mae = mean_absolute_error(y, preds)

        try:
            relevance = np.maximum(0, 21 - y.values)
            pred_relevance = np.maximum(0, 21 - preds)
            ndcg = ndcg_score(
                relevance.reshape(1, -1), pred_relevance.reshape(1, -1)
            )
        except Exception:
            ndcg = 0.0

        top3_correct = ((preds <= 3.5) & (y <= 3)).sum()
        top3_actual = (y <= 3).sum()
        top3_acc = top3_correct / top3_actual if top3_actual > 0 else 0

        top10_correct = ((preds <= 10.5) & (y <= 10)).sum()
        top10_actual = (y <= 10).sum()
        top10_acc = top10_correct / top10_actual if top10_actual > 0 else 0

        return {
            "mae": float(mae),
            "ndcg": float(ndcg),
            "top3_acc": float(top3_acc),
            "top10_acc": float(top10_acc),
        }

    @staticmethod
    def _default_prediction() -> dict:
        return {
            "expected_position": 10.0,
            "top3_probability": 0.15,
            "top10_probability": 0.50,
            "dnf_probability": 0.05,
            "expected_points": 2.0,
        }
