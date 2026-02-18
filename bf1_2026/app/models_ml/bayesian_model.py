"""Bayesian hierarchical model for F1 performance estimation."""

from __future__ import annotations

import numpy as np
from loguru import logger

try:
    import arviz as az
    import pymc as pm
except ImportError:
    pm = None  # type: ignore[assignment]
    az = None  # type: ignore[assignment]
    logger.warning("PyMC/ArviZ not installed – Bayesian model unavailable")


class BayesianF1Model:
    """
    Hierarchical Bayesian model for F1.

    Structure:
    - Global hyperprior: average championship performance
    - Driver-level prior: historical performance at circuit
    - Weather effect: increases uncertainty
    - Posterior: updated probabilities after each race
    """

    def __init__(self) -> None:
        self.trace = None
        self.model = None

    def build_model(
        self,
        drivers: list[str],
        historical_positions: dict[str, list[float]],
        circuit_difficulty: float = 0.5,
        weather_risk: float = 0.1,
    ):
        """
        Build the PyMC model.

        Args:
            drivers: list of driver identifiers
            historical_positions: {driver_id: [positions from recent races]}
            circuit_difficulty: 0-1 scale
            weather_risk: 0-1 scale
        """
        if pm is None:
            raise RuntimeError("PyMC is not installed")

        n_drivers = len(drivers)

        # Pad short histories with the mean
        obs_list = []
        max_len = max(
            (len(v) for v in historical_positions.values()), default=1
        )
        for d in drivers:
            hist = historical_positions.get(d, [10.5])
            if len(hist) < max_len:
                mean_val = np.mean(hist) if hist else 10.5
                hist = hist + [mean_val] * (max_len - len(hist))
            obs_list.append(hist)
        observed = np.array(obs_list)

        with pm.Model() as model:
            # Global hyperprior
            mu_global = pm.Normal("mu_global", mu=10.5, sigma=5)
            sigma_global = pm.HalfNormal("sigma_global", sigma=5)

            # Driver-level priors (hierarchical)
            mu_driver = pm.Normal(
                "mu_driver", mu=mu_global, sigma=sigma_global, shape=n_drivers
            )
            sigma_driver = pm.HalfNormal("sigma_driver", sigma=3, shape=n_drivers)

            # Weather effect (increases uncertainty)
            weather_effect = pm.Normal(
                "weather_effect", mu=0, sigma=weather_risk * 5
            )

            # Circuit difficulty adjustment
            circuit_effect = pm.Normal(
                "circuit_effect", mu=0, sigma=circuit_difficulty * 3
            )

            # Likelihood
            pm.Normal(
                "obs",
                mu=mu_driver[:, None] + weather_effect + circuit_effect,
                sigma=sigma_driver[:, None],
                observed=observed,
            )

        self.model = model
        logger.info(
            f"Bayesian model built: {n_drivers} drivers, "
            f"{max_len} observations each"
        )
        return model

    def fit(
        self,
        model=None,
        n_samples: int = 2000,
        n_chains: int = 2,
    ):
        """Run MCMC sampling with NUTS."""
        if pm is None:
            raise RuntimeError("PyMC is not installed")

        target_model = model or self.model
        if target_model is None:
            raise ValueError("No model built – call build_model first")

        with target_model:
            self.trace = pm.sample(
                draws=n_samples,
                chains=n_chains,
                target_accept=0.9,
                return_inferencedata=True,
                progressbar=True,
            )

        logger.info(
            f"Bayesian sampling done: {n_samples} draws × {n_chains} chains"
        )
        return self.trace

    def update_posterior(
        self,
        drivers: list[str],
        new_positions: dict[str, float],
        weather_risk: float = 0.1,
    ):
        """
        Online Bayesian update after a new race.
        Uses the current posterior as the new prior.
        """
        if self.trace is None:
            logger.warning("No existing trace – building fresh model")
            hist = {d: [new_positions.get(d, 10.5)] for d in drivers}
            model = self.build_model(drivers, hist, weather_risk=weather_risk)
            return self.fit(model, n_samples=1000)

        # Extract posterior means as new priors
        mu_post = self.trace.posterior["mu_driver"].mean(
            dim=["chain", "draw"]
        ).values

        # Build updated history using posterior + new observation
        historical: dict[str, list[float]] = {}
        for i, d in enumerate(drivers):
            prior_mean = float(mu_post[i]) if i < len(mu_post) else 10.5
            new_pos = new_positions.get(d, prior_mean)
            historical[d] = [prior_mean, new_pos]

        model = self.build_model(
            drivers, historical, weather_risk=weather_risk
        )
        return self.fit(model, n_samples=1000)

    def get_performance_probabilities(
        self,
        drivers: list[str],
        trace=None,
    ) -> dict[str, dict]:
        """
        Extract probabilities from posterior samples.

        Returns per driver:
        - top3_probability
        - top10_probability
        - expected_position
        - uncertainty (std)
        """
        if az is None:
            raise RuntimeError("ArviZ is not installed")

        trace = trace or self.trace
        if trace is None:
            return {
                d: self._default_probs() for d in drivers
            }

        mu_samples = trace.posterior["mu_driver"].values  # (chains, draws, drivers)
        # Flatten chains and draws
        flat = mu_samples.reshape(-1, mu_samples.shape[-1])

        results: dict[str, dict] = {}
        for i, driver in enumerate(drivers):
            if i >= flat.shape[1]:
                results[driver] = self._default_probs()
                continue

            samples = flat[:, i]
            expected = float(np.mean(samples))
            uncertainty = float(np.std(samples))
            top3 = float(np.mean(samples <= 3.5))
            top10 = float(np.mean(samples <= 10.5))

            results[driver] = {
                "expected_position": round(expected, 2),
                "top3_probability": round(min(top3, 1.0), 4),
                "top10_probability": round(min(top10, 1.0), 4),
                "uncertainty": round(uncertainty, 2),
            }

        return results

    @staticmethod
    def _default_probs() -> dict:
        return {
            "expected_position": 10.5,
            "top3_probability": 0.15,
            "top10_probability": 0.50,
            "uncertainty": 5.0,
        }
