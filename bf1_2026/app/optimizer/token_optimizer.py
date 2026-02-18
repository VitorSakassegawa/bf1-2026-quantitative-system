"""Token optimizer – optimal allocation of 15 BF1 tokens under constraints."""

from __future__ import annotations

import itertools
from collections import defaultdict

import numpy as np
from loguru import logger

from app.optimizer.ev_calculator import EVCalculator
from app.utils.validators import (
    BF1_MAX_TOKENS_PER_DRIVER,
    BF1_MIN_DIFFERENT_TEAMS,
    BF1_MIN_DRIVERS,
    BF1_TOTAL_TOKENS,
    validate_allocation,
)


class TokenOptimizer:
    """
    Optimize the allocation of 15 BF1 tokens across drivers.

    Hard constraints:
    - Total tokens = 15
    - Max 5 per driver
    - Min 1 per selected driver
    - At least 5 drivers from different teams
    - At least 5 drivers total
    """

    TOTAL_TOKENS = BF1_TOTAL_TOKENS
    MAX_PER_DRIVER = BF1_MAX_TOKENS_PER_DRIVER
    MIN_TEAMS = BF1_MIN_DIFFERENT_TEAMS
    MIN_DRIVERS = BF1_MIN_DRIVERS

    def __init__(self) -> None:
        self.ev_calc = EVCalculator()

    def optimize(
        self,
        predictions: dict[str, dict],
        team_membership: dict[str, str],
        strategy_type: str = "balanced",
        is_sprint: bool = False,
    ) -> dict:
        """
        Return optimal allocation: {driver_id: tokens}.

        Strategies:
        - conservative: uniform distribution, prioritizes consistency
        - balanced: maximize EV with diversification
        - aggressive: concentrate tokens on top EVs
        - ultra_aggressive: maximum concentration (5-4-3-2-1)
        """
        if not predictions:
            return {}

        # Rank drivers by expected value
        ranked = self._rank_by_ev(predictions, team_membership, is_sprint)

        if strategy_type == "conservative":
            allocation = self._conservative_allocation(ranked, team_membership)
        elif strategy_type == "aggressive":
            allocation = self._aggressive_allocation(ranked, team_membership)
        elif strategy_type == "ultra_aggressive":
            allocation = self._ultra_aggressive_allocation(ranked, team_membership)
        else:  # balanced
            allocation = self._balanced_allocation(ranked, team_membership)

        # Validate
        errors = validate_allocation(allocation, team_membership)
        if errors:
            logger.warning(f"Allocation constraint violations: {errors}")
            allocation = self._fallback_allocation(ranked, team_membership)

        total_ev = self.ev_calc.calculate_portfolio_ev(
            allocation, predictions, is_sprint
        )
        corr_penalty = self.ev_calc.calculate_correlation_penalty(
            allocation, team_membership
        )

        logger.info(
            f"Optimized allocation ({strategy_type}): "
            f"EV={total_ev:.2f}, penalty={corr_penalty:.2f}, "
            f"drivers={len(allocation)}"
        )

        return {
            "allocation": allocation,
            "total_ev": total_ev,
            "correlation_penalty": corr_penalty,
            "strategy_type": strategy_type,
        }

    def _rank_by_ev(
        self,
        predictions: dict[str, dict],
        team_membership: dict[str, str],
        is_sprint: bool,
    ) -> list[tuple[str, float, dict]]:
        """Rank drivers by single-token EV."""
        evs = []
        for did, pred in predictions.items():
            ev = self.ev_calc.calculate_driver_ev(pred, 1, is_sprint)
            evs.append((did, ev, pred))
        evs.sort(key=lambda x: x[1], reverse=True)
        return evs

    def _ensure_team_diversity(
        self,
        candidates: list[tuple[str, float, dict]],
        team_membership: dict[str, str],
        min_drivers: int = 5,
    ) -> list[str]:
        """Select at least min_drivers from different teams."""
        selected: list[str] = []
        teams_used: set[str] = set()

        for did, ev, pred in candidates:
            team = team_membership.get(did, f"unknown_{did}")
            if team not in teams_used:
                selected.append(did)
                teams_used.add(team)
            if len(teams_used) >= self.MIN_TEAMS and len(selected) >= min_drivers:
                break

        # If we need more drivers
        for did, ev, pred in candidates:
            if did not in selected:
                selected.append(did)
            if len(selected) >= min_drivers:
                break

        return selected

    def _conservative_allocation(
        self, ranked: list, team_membership: dict
    ) -> dict[str, int]:
        """Distribute tokens uniformly across more drivers."""
        drivers = self._ensure_team_diversity(ranked, team_membership, min_drivers=7)

        # Even split: 15 tokens / 7-8 drivers
        n = min(len(drivers), 8)
        drivers = drivers[:n]
        base = self.TOTAL_TOKENS // n
        remainder = self.TOTAL_TOKENS % n

        allocation = {}
        for i, did in enumerate(drivers):
            tokens = base + (1 if i < remainder else 0)
            allocation[did] = min(tokens, self.MAX_PER_DRIVER)

        return self._fix_total(allocation)

    def _balanced_allocation(
        self, ranked: list, team_membership: dict
    ) -> dict[str, int]:
        """Balance EV maximization with diversification."""
        drivers = self._ensure_team_diversity(ranked, team_membership, min_drivers=6)
        drivers = drivers[:6]

        # Weight by EV rank: [4, 3, 3, 2, 2, 1]
        weights = [4, 3, 3, 2, 2, 1]
        allocation = {}
        for i, did in enumerate(drivers):
            if i < len(weights):
                allocation[did] = min(weights[i], self.MAX_PER_DRIVER)

        return self._fix_total(allocation)

    def _aggressive_allocation(
        self, ranked: list, team_membership: dict
    ) -> dict[str, int]:
        """Concentrate tokens on top EV drivers."""
        drivers = self._ensure_team_diversity(ranked, team_membership, min_drivers=5)
        drivers = drivers[:5]

        # Concentrated: [5, 4, 3, 2, 1]
        weights = [5, 4, 3, 2, 1]
        allocation = {}
        for i, did in enumerate(drivers):
            if i < len(weights):
                allocation[did] = min(weights[i], self.MAX_PER_DRIVER)

        return self._fix_total(allocation)

    def _ultra_aggressive_allocation(
        self, ranked: list, team_membership: dict
    ) -> dict[str, int]:
        """Maximum concentration on top performers."""
        drivers = self._ensure_team_diversity(ranked, team_membership, min_drivers=5)
        drivers = drivers[:5]

        # Check EV gap
        if len(ranked) >= 2:
            ev1 = ranked[0][1]
            ev2 = ranked[1][1]
            gap = (ev1 - ev2) / abs(ev2) if ev2 != 0 else 0

            if gap > 0.2:
                # Ultra concentration
                weights = [5, 4, 3, 2, 1]
            else:
                weights = [5, 4, 3, 2, 1]
        else:
            weights = [5, 4, 3, 2, 1]

        allocation = {}
        for i, did in enumerate(drivers):
            if i < len(weights):
                allocation[did] = min(weights[i], self.MAX_PER_DRIVER)

        return self._fix_total(allocation)

    def _fallback_allocation(
        self, ranked: list, team_membership: dict
    ) -> dict[str, int]:
        """Safe fallback allocation that always passes constraints."""
        drivers = self._ensure_team_diversity(ranked, team_membership, min_drivers=5)
        drivers = drivers[:5]
        allocation = {did: 3 for did in drivers}
        return self._fix_total(allocation)

    def _fix_total(self, allocation: dict[str, int]) -> dict[str, int]:
        """Ensure total is exactly 15 tokens."""
        total = sum(allocation.values())
        drivers = list(allocation.keys())

        while total < self.TOTAL_TOKENS and drivers:
            for did in drivers:
                if allocation[did] < self.MAX_PER_DRIVER:
                    allocation[did] += 1
                    total += 1
                if total >= self.TOTAL_TOKENS:
                    break

        while total > self.TOTAL_TOKENS and drivers:
            for did in reversed(drivers):
                if allocation[did] > 1:
                    allocation[did] -= 1
                    total -= 1
                if total <= self.TOTAL_TOKENS:
                    break

        return allocation

    def check_constraints(
        self, allocation: dict[str, int], team_membership: dict[str, str]
    ) -> list[str]:
        """Public wrapper around validate_allocation."""
        return validate_allocation(allocation, team_membership)
