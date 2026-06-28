"""Train the XGBoost position + DNF models from ingested results.

Builds a chronologically-ordered training set from race_results joined with the
driver/team ELO and the persisted circuit/team KPIs, then trains and saves a
versioned model via the model registry.

LIMITATION (documented, not a bug): features such as driver_elo/team KPIs are
the *current* aggregates, so this first-pass training has mild look-ahead. The
model.train() step still uses TimeSeriesSplit on the time-ordered rows, and the
weekly scheduler retrain is where a fully point-in-time feature set should be
built. The Monte Carlo layer (ELO + grid, no training) backstops predictions
regardless of model quality.

    python -m scripts.train_model
"""

from __future__ import annotations

import asyncio

import pandas as pd
from loguru import logger
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.circuit import Circuit
from app.models.driver import Driver
from app.models.race import Race
from app.models.race_result import RaceResult
from app.models.team import Team
from app.models_ml.xgboost_model import FEATURE_COLUMNS, XGBoostF1Model

# Defaults for features we cannot reconstruct point-in-time in this first pass.
DEFAULTS = {
    "weather_risk_index": 0.1,
    "driver_dnf_rate_circuit": 0.05,
    "driver_consistency_index": 0.5,
    "driver_momentum": 1.0,
    "driver_volatility": 0.3,
    "driver_wet_performance": 0.0,
    "temperature_air": 25.0,
    "humidity": 50.0,
    "driver_avg_positions_gained": 0.0,
}


async def _load_rows() -> list[dict]:
    async with AsyncSessionLocal() as session:
        drivers = {
            d.id: d for d in (await session.execute(select(Driver))).scalars().all()
        }
        teams = {
            t.id: t for t in (await session.execute(select(Team))).scalars().all()
        }
        circuits = {
            c.id: c for c in (await session.execute(select(Circuit))).scalars().all()
        }

        races = (
            await session.execute(select(Race).order_by(Race.race_date))
        ).scalars().all()

        rows: list[dict] = []
        for race in races:
            circuit = circuits.get(race.circuit_id)
            results = (
                await session.execute(
                    select(RaceResult).where(RaceResult.race_id == race.id)
                )
            ).scalars().all()

            for r in results:
                driver = drivers.get(r.driver_id)
                if driver is None:
                    continue
                team = teams.get(driver.team_id)

                feat = dict(DEFAULTS)
                feat.update(
                    {
                        "grid_position": r.grid_position or 10,
                        "driver_elo": driver.current_elo,
                        "team_elo": team.current_elo if team else 1500.0,
                        "circuit_avg_dnf_rate": (circuit.avg_dnf_rate if circuit else None) or 0.1,
                        "circuit_overtaking_index": (
                            circuit.avg_overtaking_index if circuit else None
                        ) or 0.5,
                        "team_reliability_index": (
                            team.reliability_index if team else None
                        ) or 0.95,
                        "team_strategic_error_rate": (
                            team.strategic_error_rate if team else None
                        ) or 0.1,
                        "is_sprint": int(race.is_sprint_weekend),
                    }
                )
                # Target: DNF treated as P20 for the regressor.
                feat["_y"] = 20 if (r.dnf or r.final_position is None) else r.final_position
                feat["_dnf"] = int(r.dnf)
                rows.append(feat)

        return rows


async def train() -> None:
    rows = await _load_rows()
    if len(rows) < 100:
        logger.warning(
            f"Only {len(rows)} training rows — ingest more history before training. "
            "Skipping (Monte Carlo still works without a trained model)."
        )
        return

    df = pd.DataFrame(rows)
    X = df[FEATURE_COLUMNS].astype(float)
    y = df["_y"].astype(float)
    y_dnf = df["_dnf"].astype(int)

    model = XGBoostF1Model()
    try:
        metrics = model.train(X, y)
        model.train_dnf_classifier(X, y_dnf)
    except RuntimeError as e:
        logger.error(f"Training aborted: {e}")
        return

    path = model.save_model()
    logger.info(
        f"Model trained on {len(rows)} rows: MAE={metrics.get('mae', 0):.3f}, "
        f"top3_acc={metrics.get('top3_acc', 0):.2%}; saved to {path}"
    )


if __name__ == "__main__":
    asyncio.run(train())
