"""Strategy engine – from a grid to a bet.

    drivers (grid, form, rating, reliability)
        -> race simulation            (race_model, calibrated on real results)
        -> chip allocation            (pool_optimizer, exact under the pool rules)
        -> 11th-place pick            (most probable P11 in the simulation)

The XGBoost model is deliberately no longer on this path. It was never trained
on point-in-time features (its training set had look-ahead and ten constant
columns), and a walk-forward backtest showed the starting grid already carries
almost all of the predictive signal. Predictions here come from the simulator
alone, whose every parameter was fitted and checked out-of-sample.
"""

from __future__ import annotations

from loguru import logger

from app.models_ml.monte_carlo import MonteCarloSimulator, SimulationConfig
from app.models_ml.race_model import DEFAULT_PARAMS, ModelParams
from app.optimizer.ev_calculator import EVCalculator
from app.optimizer.token_optimizer import TokenOptimizer
from app.rules.pool_rules import DEFAULT_RULES, PoolRules
from app.utils.guardrails import PitStopProjector, validate_prediction


class StrategyEngine:
    """Simulate a session and recommend a bet for it."""

    def __init__(
        self,
        rules: PoolRules = DEFAULT_RULES,
        params: ModelParams = DEFAULT_PARAMS,
    ) -> None:
        self.rules = rules
        self.params = params
        self.mc_simulator = MonteCarloSimulator(rules)
        self.ev_calc = EVCalculator(rules)
        self.optimizer = TokenOptimizer(rules)
        self.pit_projector = PitStopProjector()

    def generate_strategy(
        self,
        drivers: list[dict],
        circuit_kpis: dict | None,
        weather: dict | None,
        team_membership: dict[str, str],
        user_aggressiveness: str = "balanced",
        is_sprint: bool = False,
        n_simulations: int = 20000,
        is_sprint_weekend: bool | None = None,
        confidence: float = 0.6,
        seed: int = 42,
    ) -> dict:
        """
        Build the recommended bet for one session.

        ``drivers`` items: driver_id, team_id, name, code, grid_position
        (None before qualifying), elo, and optionally form / dnf_probability
        (see ``race_model.FormBook``).

        ``is_sprint`` means the session being bet on *is* the sprint; it
        selects the sprint points table and the doubled score.
        ``is_sprint_weekend`` only informs the pit-stop projection.
        """
        circuit_kpis = circuit_kpis or {}
        weather = weather or {}
        logger.info(
            f"Generating strategy: {len(drivers)} drivers, "
            f"profile={user_aggressiveness}, sprint={is_sprint}"
        )

        predictions = self.mc_simulator.simulate_race(
            drivers,
            circuit_kpis,
            weather,
            SimulationConfig(
                n_simulations=n_simulations, random_seed=seed, params=self.params
            ),
            is_sprint,
        )
        sim = self.mc_simulator.last_simulation
        predictions = {
            did: validate_prediction(
                pred, grid_size=len(drivers), is_sprint=is_sprint, context=f"strategy:{did}"
            )
            for did, pred in predictions.items()
        }

        opt = self.optimizer.optimize(
            predictions,
            team_membership,
            strategy_type=user_aggressiveness,
            is_sprint=is_sprint,
            simulation=sim,
        )
        allocation: dict[str, int] = opt.get("allocation", {})
        info = {d["driver_id"]: d for d in drivers}

        def describe(did: str) -> dict:
            d = info.get(did, {})
            return {
                "driver_id": did,
                "driver_name": d.get("name", "Unknown"),
                "driver_code": d.get("code", "???"),
                "team_id": d.get("team_id"),
                "team_name": d.get("team_name"),
            }

        def chip_ev(did: str) -> float:
            return self.ev_calc.calculate_driver_ev(
                predictions.get(did, {}), allocation[did], is_sprint
            )

        driver_details = []
        # Most chips first; among equals, the one worth more.
        for did, tokens in sorted(
            allocation.items(), key=lambda kv: (-kv[1], -chip_ev(kv[0]))
        ):
            pred = predictions.get(did, {})
            driver_details.append(
                {
                    **describe(did),
                    "tokens": tokens,
                    "grid_position": info.get(did, {}).get("grid_position"),
                    "expected_value": self.ev_calc.calculate_driver_ev(pred, tokens, is_sprint),
                    "expected_points": pred.get("expected_points", 0.0),
                    "win_probability": pred.get("win_probability", 0.0),
                    "top3_probability": pred.get("top3_probability", 0.0),
                    "top10_probability": pred.get("top10_probability", 0.0),
                    "dnf_probability": pred.get("dnf_probability", 0.0),
                    "expected_position": pred.get("expected_position", 10),
                }
            )

        # The whole field in predicted finishing order, for display.
        field_view = sorted(
            (
                {
                    **describe(did),
                    "grid_position": info.get(did, {}).get("grid_position"),
                    "expected_position": pred.get("expected_position"),
                    "expected_points": pred.get("expected_points", 0.0),
                    "win_probability": pred.get("win_probability", 0.0),
                    "top3_probability": pred.get("top3_probability", 0.0),
                    "top10_probability": pred.get("top10_probability", 0.0),
                    "eleventh_probability": pred.get("eleventh_probability", 0.0),
                    "dnf_probability": pred.get("dnf_probability", 0.0),
                }
                for did, pred in predictions.items()
            ),
            key=lambda d: d["expected_position"] or 99,
        )

        eleventh_candidates = [
            {**describe(did), "probability": round(p, 4)}
            for did, p in opt.get("eleventh_candidates", [])
        ]
        eleventh_pick = eleventh_candidates[0] if eleventh_candidates else None

        if is_sprint_weekend is None:
            is_sprint_weekend = is_sprint
        pit = self.pit_projector.project(
            tire_degradation=circuit_kpis.get("avg_tire_degradation"),
            historical_avg_stops=circuit_kpis.get("avg_pit_stops"),
            confidence=confidence,
            is_sprint_weekend=is_sprint_weekend,
            is_sprint_race=is_sprint,
        )

        grid_provisional = bool(sim.grid_provisional) if sim is not None else False
        insights = self._generate_insights(
            driver_details, eleventh_pick, weather, grid_provisional, predictions, info
        )
        insights.append(
            pit.note
            or f"Paradas previstas: {pit.label}"
        )

        strategy = {
            "allocation": allocation,
            "driver_details": driver_details,
            "eleventh_pick": eleventh_pick,
            "eleventh_candidates": eleventh_candidates,
            # Expected chip score plus the expected value of the 11th-place pick.
            "total_ev": opt.get("expected_total", opt.get("total_ev", 0.0)),
            "expected_chip_score": opt.get("total_ev", 0.0),
            "score_range": {
                "p10": opt.get("p10"),
                "p50": opt.get("p50"),
                "p90": opt.get("p90"),
                "std": opt.get("std"),
            },
            "max_score": self.rules.max_score(is_sprint),
            "strategy_type": opt.get("strategy_type", user_aggressiveness),
            "constraint_errors": opt.get("constraint_errors", []),
            "grid_provisional": grid_provisional,
            "is_sprint": is_sprint,
            "n_simulations": n_simulations,
            "field": field_view,
            "pit_stop_projection": {
                "projected_stops": pit.projected_stops,
                "label": pit.label,
                "raw_estimate": pit.raw_estimate,
                "capped": pit.capped,
                "note": pit.note,
            },
            "insights": insights,
        }
        logger.info(
            f"Strategy generated: expected {strategy['total_ev']:.1f} of "
            f"{strategy['max_score']:.0f}, {len(allocation)} drivers"
        )
        return strategy

    def _generate_insights(
        self,
        driver_details: list[dict],
        eleventh_pick: dict | None,
        weather: dict,
        grid_provisional: bool,
        predictions: dict[str, dict],
        info: dict[str, dict],
    ) -> list[str]:
        """Short human-readable notes on the recommendation."""
        insights: list[str] = []

        if grid_provisional:
            insights.append(
                "Ainda sem classificação — grid estimado pela forma recente. "
                "Refaça depois do qualifying: o grid é o dado que mais pesa."
            )

        if driver_details:
            top = driver_details[0]
            insights.append(
                f"Principal aposta: {top['driver_code']} "
                f"(vitória {top['win_probability']:.0%}, pódio {top['top3_probability']:.0%})"
            )

        risky = [
            d for d in driver_details if d["dnf_probability"] >= 0.25
        ]
        for d in risky:
            insights.append(
                f"{d['driver_code']} abandona em {d['dnf_probability']:.0%} das simulações "
                "— já está na conta, mas é um risco conhecido"
            )

        if eleventh_pick:
            insights.append(
                f"11º lugar: {eleventh_pick['driver_code']} "
                f"({eleventh_pick['probability']:.0%}). Nenhum palpite é favorito — "
                "o melhor candidato acerta cerca de uma vez em dez."
            )

        rain = float(weather.get("rain_probability", 0.0) or 0.0)
        if float(weather.get("weather_risk_index", 0.0) or 0.0) > 0.3 or rain > 0.4:
            insights.append(
                f"Risco de chuva ({rain:.0%}) — resultados mais dispersos que o normal"
            )
        return insights

    def format_strategy_report(self, strategy: dict) -> str:
        """Plain-text rendering of a strategy."""
        lines = ["━━━━━━━━━━━━━━━━━", "TOKEN ALLOCATION", "━━━━━━━━━━━━━━━━━"]
        cap = self.rules.max_chips_per_driver
        for d in strategy.get("driver_details", []):
            bar = "●" * d["tokens"] + "○" * (cap - d["tokens"])
            lines.append(
                f"{d['driver_code']}: [{bar}] {d['tokens']}T "
                f"| EV: {d['expected_value']:.1f} "
                f"| Top3: {d['top3_probability']:.0%} "
                f"| DNF: {d['dnf_probability']:.0%}"
            )
        pick = strategy.get("eleventh_pick")
        if pick:
            lines.append(f"11th: {pick['driver_code']} ({pick['probability']:.0%})")
        lines.append("━━━━━━━━━━━━━━━━━")
        lines.append(
            f"Expected: {strategy.get('total_ev', 0):.1f} of "
            f"{strategy.get('max_score', 0):.0f} | "
            f"Profile: {strategy.get('strategy_type', 'balanced')}"
        )
        insights = strategy.get("insights", [])
        if insights:
            lines.append("")
            lines.append("INSIGHTS")
            lines.extend(f"  {i}" for i in insights)
        return "\n".join(lines)
