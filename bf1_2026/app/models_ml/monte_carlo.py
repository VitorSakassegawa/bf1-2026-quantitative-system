"""Monte Carlo race simulator – 20,000 simulations per run."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
from loguru import logger

from app.utils.guardrails import (
    MAX_DNF_PROBABILITY,
    clamp_expected_points,
    clamp_position,
    clamp_probability,
)
from app.utils.validators import BF1_POINTS_TABLE, BF1_DNF_PENALTY


@dataclass
class SimulationConfig:
    n_simulations: int = 20000
    random_seed: int = 42
    safety_car_probability: float = 0.35
    safety_car_position_variance: float = 2.0
    grid_noise_sigma: float = 1.5


class MonteCarloSimulator:
    """
    Monte Carlo race simulator.

    Each simulation:
    1. Adds Gaussian noise to grid positions
    2. Applies stochastic events (DNF, safety car, rain)
    3. Resolves final positions
    4. Calculates BF1 points
    """

    SPRINT_MULTIPLIER = 2.0
    FASTEST_LAP_BONUS = 1

    def simulate_race(
        self,
        drivers: list[dict],
        circuit_kpis: dict,
        weather: dict,
        config: SimulationConfig | None = None,
        is_sprint: bool = False,
    ) -> dict:
        """
        Run N Monte Carlo simulations of a race.

        Args:
            drivers: [{driver_id, grid_position, dnf_probability, elo,
                       wet_performance, team_reliability, ...}]
            circuit_kpis: circuit-level KPIs
            weather: weather data dict
            config: simulation config
            is_sprint: whether this is a sprint race

        Returns:
            {driver_id: {avg_position, top3_probability, top10_probability,
                         dnf_probability_simulated, expected_bf1_points,
                         position_distribution, expected_value}}
        """
        if config is None:
            config = SimulationConfig()

        start = time.time()
        rng = np.random.default_rng(config.random_seed)
        n_drivers = len(drivers)
        n_sims = config.n_simulations

        weather_risk = weather.get("weather_risk_index", 0.0)
        sc_prob = config.safety_car_probability + circuit_kpis.get("avg_dnf_rate", 0.0) * 0.5

        # Pre-allocate result arrays
        positions_all = np.zeros((n_sims, n_drivers), dtype=np.int32)
        dnf_all = np.zeros((n_sims, n_drivers), dtype=bool)
        points_all = np.zeros((n_sims, n_drivers), dtype=np.float64)

        # Driver arrays for vectorized ops
        grid_arr = np.array([d.get("grid_position", 10) for d in drivers], dtype=np.float64)
        dnf_probs = np.array([d.get("dnf_probability", 0.05) for d in drivers], dtype=np.float64)
        elo_arr = np.array([d.get("elo", 1500) for d in drivers], dtype=np.float64)
        wet_perf = np.array([d.get("wet_performance", 0.0) for d in drivers], dtype=np.float64)

        # Adjust DNF probs for weather, then clamp back into a possible range.
        # Without the clamp a wet race (weather_risk=0.8) on an already
        # unreliable car (0.6) produced p=1.08 — a guaranteed retirement for
        # every driver in every simulation, pinning the whole field at -10.
        dnf_probs_adj = np.clip(
            dnf_probs * (1.0 + weather_risk), 0.0, MAX_DNF_PROBABILITY
        )

        for sim in range(n_sims):
            result = self._single_simulation(
                grid_arr, dnf_probs_adj, elo_arr, wet_perf,
                sc_prob, weather_risk, config, rng, is_sprint,
            )
            positions_all[sim] = result["positions"]
            dnf_all[sim] = result["dnfs"]
            points_all[sim] = result["points"]

        # Aggregate results
        results: dict = {}

        for i, driver in enumerate(drivers):
            did = driver["driver_id"]
            pos_series = positions_all[:, i]
            dnf_series = dnf_all[:, i]
            pts_series = points_all[:, i]

            # Position distribution histogram (1..n_drivers). Hard-coding 20
            # slots silently discarded P21/P22 outcomes on a 22-car grid, so
            # the distribution no longer summed to n_sims.
            pos_dist = np.bincount(
                pos_series, minlength=n_drivers + 1
            )[1:n_drivers + 1].tolist()

            # avg_position must use the same (unconditional) denominator as the
            # probabilities below. Averaging only over finishes made a driver
            # who retires 9 races in 10 look like a P1.35 car.
            avg_pos = float(pos_series.mean()) if len(pos_series) else float(n_drivers)
            top3_prob = float((pos_series <= 3).mean())
            top10_prob = float((pos_series <= 10).mean())
            dnf_sim = float(dnf_series.mean())

            # `pts_series` already carries BF1_DNF_PENALTY for retirements, so
            # this mean is the full unconditional E[points] for the session.
            # The sprint multiplier is deliberately NOT applied here — it is
            # applied once, in EVCalculator. Applying it in both places scaled
            # sprint EV by 4x.
            avg_pts = float(pts_series.mean())

            avg_pts = clamp_expected_points(avg_pts, is_sprint=False)
            results[did] = {
                "avg_position": round(clamp_position(avg_pos, n_drivers), 2),
                "top3_probability": round(clamp_probability(top3_prob), 4),
                "top10_probability": round(clamp_probability(top10_prob), 4),
                "dnf_probability_simulated": round(clamp_probability(dnf_sim), 4),
                "expected_bf1_points": round(avg_pts, 2),
                "position_distribution": pos_dist,
                "expected_value": round(avg_pts, 2),
            }

        elapsed = time.time() - start
        logger.info(
            f"Monte Carlo: {n_sims} sims, {n_drivers} drivers in {elapsed:.2f}s"
        )
        return results

    def _single_simulation(
        self,
        grid_arr: np.ndarray,
        dnf_probs: np.ndarray,
        elo_arr: np.ndarray,
        wet_perf: np.ndarray,
        sc_prob: float,
        weather_risk: float,
        config: SimulationConfig,
        rng: np.random.Generator,
        is_sprint: bool,
    ) -> dict:
        """Run a single race simulation."""
        n = len(grid_arr)

        # 1) Gaussian noise on grid
        noise = rng.normal(0, config.grid_noise_sigma, n)
        scores = grid_arr + noise

        # 2) Apply weather effects
        if weather_risk > 0.2:
            weather_noise = rng.normal(0, weather_risk * 3, n)
            wet_bonus = -wet_perf * weather_risk * 2  # Lower score = better
            scores += weather_noise + wet_bonus

        # 3) ELO adjustment (better ELO → lower score)
        elo_factor = -(elo_arr - 1500) / 400.0
        scores += elo_factor * 0.5

        # 4) DNF check (Bernoulli per driver)
        dnfs = rng.random(n) < dnf_probs

        # 5) Safety car effect
        if rng.random() < sc_prob:
            sc_noise = rng.normal(0, config.safety_car_position_variance, n)
            scores += sc_noise

        # 6) Resolve positions
        positions = np.zeros(n, dtype=np.int32)
        finishers = np.where(~dnfs)[0]
        non_finishers = np.where(dnfs)[0]

        if len(finishers) > 0:
            finish_order = finishers[np.argsort(scores[finishers])]
            for rank, idx in enumerate(finish_order, 1):
                positions[idx] = rank

        # DNFs get positions after finishers
        start_pos = len(finishers) + 1
        for idx in non_finishers:
            positions[idx] = start_pos
            start_pos += 1

        # 7) Calculate BF1 points
        points = np.zeros(n, dtype=np.float64)
        for i in range(n):
            if dnfs[i]:
                points[i] = BF1_DNF_PENALTY
            else:
                points[i] = BF1_POINTS_TABLE.get(positions[i], 0)

        return {"positions": positions, "dnfs": dnfs, "points": points}

    def calculate_expected_value(
        self,
        simulation_results: dict,
        token_allocation: int,
        is_sprint: bool = False,
    ) -> float:
        """EV = tokens * E[points] * sprint_multiplier.

        `expected_bf1_points` is already the unconditional expectation over all
        simulations, with BF1_DNF_PENALTY included for the retirement draws.
        Re-weighting it by (1 - dnf_prob) and then subtracting the penalty a
        second time charged every retirement twice.
        """
        avg_pts = simulation_results.get("expected_bf1_points", 0.0)

        mult = self.SPRINT_MULTIPLIER if is_sprint else 1.0
        ev = token_allocation * avg_pts * mult
        return round(ev, 2)
