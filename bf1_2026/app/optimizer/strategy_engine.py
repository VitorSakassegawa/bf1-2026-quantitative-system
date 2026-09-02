"""Strategy engine – orchestrates the full prediction-to-allocation pipeline."""

from __future__ import annotations

from loguru import logger

from app.kpis.circuit_kpis import CircuitKPICalculator
from app.kpis.derived_kpis import DerivedKPICalculator
from app.kpis.driver_kpis import DriverKPICalculator
from app.kpis.team_kpis import TeamKPICalculator
from app.models_ml.monte_carlo import MonteCarloSimulator, SimulationConfig
from app.models_ml.xgboost_model import XGBoostF1Model
from app.optimizer.ev_calculator import EVCalculator
from app.optimizer.token_optimizer import TokenOptimizer
from app.utils.guardrails import PitStopProjector, validate_prediction
from app.utils.validators import BF1_DNF_PENALTY


def _net_expected_points(scoring_points: float, dnf_prob: float) -> float:
    """Fold the retirement branch into a raw scoring-points estimate.

    Returns the unconditional expectation E[points] = P(finish)*points +
    P(dnf)*penalty, which is the form EVCalculator expects.
    """
    p_dnf = min(max(float(dnf_prob), 0.0), 1.0)
    return (1.0 - p_dnf) * float(scoring_points) + p_dnf * float(BF1_DNF_PENALTY)


class StrategyEngine:
    """
    Main strategy orchestrator.
    Combines: KPIs + ML + Monte Carlo + Optimisation + BF1 Rules.
    """

    def __init__(self) -> None:
        self.circuit_kpi_calc = CircuitKPICalculator()
        self.driver_kpi_calc = DriverKPICalculator()
        self.team_kpi_calc = TeamKPICalculator()
        self.derived_kpi_calc = DerivedKPICalculator()
        self.xgb_model = XGBoostF1Model()
        self.mc_simulator = MonteCarloSimulator()
        self.ev_calc = EVCalculator()
        self.optimizer = TokenOptimizer()
        self.pit_projector = PitStopProjector()

    def generate_strategy(
        self,
        drivers: list[dict],
        circuit_kpis: dict,
        weather: dict,
        team_membership: dict[str, str],
        user_aggressiveness: str = "balanced",
        is_sprint: bool = False,
        n_simulations: int = 20000,
        is_sprint_weekend: bool | None = None,
        confidence: float = 0.6,
    ) -> dict:
        """
        Full strategy pipeline:
        1. Build feature matrix from KPIs
        2. XGBoost predictions
        3. Monte Carlo simulations
        4. Calculate EVs
        5. Optimize token allocation
        6. Return complete strategy with justification
        """
        logger.info(
            f"Generating strategy: {len(drivers)} drivers, "
            f"aggressiveness={user_aggressiveness}"
        )

        # Step 1-2: XGBoost predictions
        xgb_predictions = {}
        try:
            race_data = {"drivers": drivers, "is_sprint": is_sprint}
            driver_kpis = {d["driver_id"]: d.get("kpis", {}) for d in drivers}
            team_kpis_map = {d.get("team_id", ""): d.get("team_kpis", {}) for d in drivers}

            X = self.xgb_model.build_feature_matrix(
                race_data, driver_kpis, team_kpis_map, circuit_kpis, weather
            )
            preds = self.xgb_model.predict(X)
            for i, driver in enumerate(drivers):
                xgb_predictions[driver["driver_id"]] = preds[i]
        except Exception as e:
            logger.warning(f"XGBoost prediction failed, using defaults: {e}")
            for d in drivers:
                xgb_predictions[d["driver_id"]] = {
                    "expected_position": d.get("grid_position", 10),
                    "top3_probability": 0.15,
                    "top10_probability": 0.50,
                    "dnf_probability": 0.05,
                    "expected_points": 2.0,
                }

        # Step 3: Monte Carlo simulation
        mc_results = {}
        try:
            mc_drivers = []
            for d in drivers:
                pred = xgb_predictions.get(d["driver_id"], {})
                mc_drivers.append({
                    "driver_id": d["driver_id"],
                    "grid_position": d.get("grid_position", 10),
                    "dnf_probability": pred.get("dnf_probability", 0.05),
                    "elo": d.get("elo", 1500),
                    "wet_performance": d.get("kpis", {}).get("wet_performance", 0.0),
                    "team_reliability": d.get("team_kpis", {}).get(
                        "mechanical_reliability", 0.95
                    ),
                })

            config = SimulationConfig(n_simulations=n_simulations)
            mc_results = self.mc_simulator.simulate_race(
                mc_drivers, circuit_kpis, weather, config, is_sprint
            )
        except Exception as e:
            logger.warning(f"Monte Carlo failed: {e}")

        # Step 4: Merge predictions – prefer MC where available
        merged_predictions: dict[str, dict] = {}
        for did, xgb_pred in xgb_predictions.items():
            mc_pred = mc_results.get(did, {})
            merged_predictions[did] = {
                "expected_position": mc_pred.get(
                    "avg_position", xgb_pred.get("expected_position", 10)
                ),
                "top3_probability": mc_pred.get(
                    "top3_probability", xgb_pred.get("top3_probability", 0.15)
                ),
                "top10_probability": mc_pred.get(
                    "top10_probability", xgb_pred.get("top10_probability", 0.5)
                ),
                "dnf_probability": mc_pred.get(
                    "dnf_probability_simulated",
                    xgb_pred.get("dnf_probability", 0.05),
                ),
                # Must be the unconditional E[points] (DNF penalty included,
                # sprint multiplier NOT applied) — see EVCalculator's contract.
                # Monte Carlo already reports it in that form; the XGBoost
                # fallback reports raw scoring points, so net it here rather
                # than letting two different quantities share one key.
                "expected_points": mc_pred.get(
                    "expected_bf1_points",
                    _net_expected_points(
                        xgb_pred.get("expected_points", 2.0),
                        xgb_pred.get("dnf_probability", 0.05),
                    ),
                ),
                "expected_value": mc_pred.get("expected_value", 0),
            }
            # Guardrail: clamp any out-of-domain field before it influences EV.
            merged_predictions[did] = validate_prediction(
                merged_predictions[did],
                grid_size=len(drivers),
                is_sprint=is_sprint,
                context=f"strategy:{did}",
            )

        # Step 5: Optimize allocation
        opt_result = self.optimizer.optimize(
            merged_predictions,
            team_membership,
            strategy_type=user_aggressiveness,
            is_sprint=is_sprint,
        )

        # Adjust for extreme weather
        weather_risk = weather.get("weather_risk_index", 0.0)
        if weather_risk > 0.6 and user_aggressiveness != "conservative":
            logger.info("High weather risk – switching to conservative")
            opt_result = self.optimizer.optimize(
                merged_predictions,
                team_membership,
                strategy_type="conservative",
                is_sprint=is_sprint,
            )
            opt_result["weather_adjusted"] = True

        # Step 6: Build strategy report data
        allocation = opt_result.get("allocation", {})
        driver_details = []
        for did, tokens in sorted(
            allocation.items(), key=lambda x: x[1], reverse=True
        ):
            pred = merged_predictions.get(did, {})
            driver_info = next((d for d in drivers if d["driver_id"] == did), {})
            driver_details.append({
                "driver_id": did,
                "driver_name": driver_info.get("name", "Unknown"),
                "driver_code": driver_info.get("code", "???"),
                "tokens": tokens,
                "expected_value": self.ev_calc.calculate_driver_ev(
                    pred, tokens, is_sprint
                ),
                "top3_probability": pred.get("top3_probability", 0),
                "top10_probability": pred.get("top10_probability", 0),
                "dnf_probability": pred.get("dnf_probability", 0),
                "expected_position": pred.get("expected_position", 10),
            })

        # Bounded pit-stop projection (never emits a physically impossible count).
        if is_sprint_weekend is None:
            is_sprint_weekend = is_sprint
        pit_projection = self.pit_projector.project(
            tire_degradation=circuit_kpis.get("avg_tire_degradation"),
            historical_avg_stops=circuit_kpis.get("avg_pit_stops"),
            confidence=confidence,
            is_sprint_weekend=is_sprint_weekend,
            is_sprint_race=is_sprint,
        )

        insights = self._generate_insights(
            driver_details, weather, circuit_kpis, weather_risk
        )
        # Surface the pit-stop guardrail note so the user understands a capped value.
        if pit_projection.note:
            insights.append(pit_projection.note)
        else:
            insights.append(
                f"Projected strategy: {pit_projection.label} "
                f"(confidence {pit_projection.confidence:.0%})"
            )

        strategy = {
            "allocation": allocation,
            "driver_details": driver_details,
            "total_ev": opt_result.get("total_ev", 0),
            "correlation_penalty": opt_result.get("correlation_penalty", 0),
            "strategy_type": opt_result.get("strategy_type", user_aggressiveness),
            "weather_adjusted": opt_result.get("weather_adjusted", False),
            "is_sprint": is_sprint,
            "n_simulations": n_simulations,
            "pit_stop_projection": {
                "projected_stops": pit_projection.projected_stops,
                "label": pit_projection.label,
                "raw_estimate": pit_projection.raw_estimate,
                "capped": pit_projection.capped,
                "note": pit_projection.note,
            },
            "insights": insights,
        }

        logger.info(
            f"Strategy generated: EV={strategy['total_ev']:.2f}, "
            f"{len(allocation)} drivers"
        )
        return strategy

    def _generate_insights(
        self,
        driver_details: list[dict],
        weather: dict,
        circuit_kpis: dict,
        weather_risk: float,
    ) -> list[str]:
        """Generate human-readable insights for the strategy."""
        insights: list[str] = []

        if weather_risk > 0.6:
            insights.append(
                f"High rain probability ({weather.get('rain_probability', 0):.0%}) "
                "– strategy diversified to reduce risk"
            )
        elif weather_risk > 0.3:
            insights.append("Moderate weather risk – monitor conditions before race")

        ot_index = circuit_kpis.get("avg_overtaking_index", 0.5)
        if ot_index > 0.6:
            insights.append(
                "High overtaking circuit – grid position less important"
            )
        elif ot_index < 0.3:
            insights.append(
                "Low overtaking – qualifying position is critical"
            )

        dnf_rate = circuit_kpis.get("avg_dnf_rate", 0.1)
        if dnf_rate > 0.2:
            insights.append(
                f"High DNF rate ({dnf_rate:.0%}) at this circuit – "
                "consider reliability in picks"
            )

        if driver_details:
            top = driver_details[0]
            insights.append(
                f"Top pick: {top['driver_code']} "
                f"(EV={top['expected_value']:.1f}, "
                f"top3={top['top3_probability']:.0%})"
            )

        return insights

    def format_strategy_report(self, strategy: dict) -> str:
        """Format strategy as a readable Telegram message."""
        lines = ["━━━━━━━━━━━━━━━━━"]
        lines.append("TOKEN ALLOCATION")
        lines.append("━━━━━━━━━━━━━━━━━")

        for d in strategy.get("driver_details", []):
            tokens_bar = "●" * d["tokens"] + "○" * (5 - d["tokens"])
            lines.append(
                f"{d['driver_code']}: [{tokens_bar}] {d['tokens']}T "
                f"| EV: {d['expected_value']:.1f} "
                f"| Top3: {d['top3_probability']:.0%} "
                f"| DNF: {d['dnf_probability']:.0%}"
            )

        lines.append("━━━━━━━━━━━━━━━━━")
        lines.append(
            f"Total EV: {strategy.get('total_ev', 0):.2f} | "
            f"Strategy: {strategy.get('strategy_type', 'balanced')}"
        )

        if strategy.get("weather_adjusted"):
            lines.append("Weather adjusted: YES (high rain risk)")

        insights = strategy.get("insights", [])
        if insights:
            lines.append("")
            lines.append("INSIGHTS")
            for i in insights:
                lines.append(f"  {i}")

        return "\n".join(lines)
