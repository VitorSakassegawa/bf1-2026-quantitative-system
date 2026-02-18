"""Tests for token optimizer – validate all BF1-2026 constraints."""

import pytest

from app.optimizer.ev_calculator import EVCalculator
from app.optimizer.token_optimizer import TokenOptimizer
from app.utils.validators import (
    BF1_MAX_TOKENS_PER_DRIVER,
    BF1_MIN_DIFFERENT_TEAMS,
    BF1_MIN_DRIVERS,
    BF1_TOTAL_TOKENS,
    validate_allocation,
)


@pytest.fixture
def optimizer():
    return TokenOptimizer()


@pytest.fixture
def ev_calc():
    return EVCalculator()


@pytest.fixture
def predictions_20():
    """Predictions for 20 drivers with varying EVs."""
    preds = {}
    for i in range(1, 21):
        preds[f"d{i}"] = {
            "top3_probability": max(0, (21 - i) / 20 * 0.8),
            "top10_probability": max(0, (21 - i) / 20),
            "dnf_probability": 0.05,
            "expected_points": max(0, (21 - i) * 1.2),
            "expected_position": float(i),
        }
    return preds


@pytest.fixture
def team_membership_20():
    """20 drivers across 10 teams (2 per team)."""
    teams = {}
    for i in range(1, 21):
        team_num = (i - 1) // 2 + 1
        teams[f"d{i}"] = f"team_{team_num}"
    return teams


# ---------- Constraint Validation Tests ----------


class TestConstraints:
    def test_valid_allocation(self):
        alloc = {"d1": 5, "d2": 4, "d3": 3, "d4": 2, "d5": 1}
        teams = {
            "d1": "t1", "d2": "t2", "d3": "t3", "d4": "t4", "d5": "t5"
        }
        errors = validate_allocation(alloc, teams)
        assert errors == []

    def test_total_not_15(self):
        alloc = {"d1": 5, "d2": 4, "d3": 3, "d4": 2}  # total = 14
        teams = {"d1": "t1", "d2": "t2", "d3": "t3", "d4": "t4"}
        errors = validate_allocation(alloc, teams)
        assert any("15" in e for e in errors)

    def test_exceeds_max_per_driver(self):
        alloc = {"d1": 6, "d2": 3, "d3": 3, "d4": 2, "d5": 1}
        teams = {
            "d1": "t1", "d2": "t2", "d3": "t3", "d4": "t4", "d5": "t5"
        }
        errors = validate_allocation(alloc, teams)
        assert any("maximum" in e.lower() for e in errors)

    def test_below_min_per_driver(self):
        alloc = {"d1": 5, "d2": 4, "d3": 3, "d4": 3, "d5": 0}
        teams = {
            "d1": "t1", "d2": "t2", "d3": "t3", "d4": "t4", "d5": "t5"
        }
        errors = validate_allocation(alloc, teams)
        assert any("minimum" in e.lower() for e in errors)

    def test_fewer_than_5_drivers(self):
        alloc = {"d1": 5, "d2": 5, "d3": 5}
        teams = {"d1": "t1", "d2": "t2", "d3": "t3"}
        errors = validate_allocation(alloc, teams)
        assert any("5 drivers" in e for e in errors)

    def test_fewer_than_5_teams(self):
        alloc = {"d1": 3, "d2": 3, "d3": 3, "d4": 3, "d5": 3}
        # All from same team
        teams = {
            "d1": "t1", "d2": "t1", "d3": "t1", "d4": "t1", "d5": "t1"
        }
        errors = validate_allocation(alloc, teams)
        assert any("team" in e.lower() for e in errors)


# ---------- Optimizer Tests ----------


class TestTokenOptimizer:
    def test_optimize_returns_valid(
        self, optimizer, predictions_20, team_membership_20
    ):
        result = optimizer.optimize(
            predictions_20, team_membership_20, "balanced"
        )
        alloc = result["allocation"]

        assert sum(alloc.values()) == BF1_TOTAL_TOKENS
        assert all(1 <= v <= BF1_MAX_TOKENS_PER_DRIVER for v in alloc.values())
        assert len(alloc) >= BF1_MIN_DRIVERS

        teams_used = {team_membership_20[d] for d in alloc}
        assert len(teams_used) >= BF1_MIN_DIFFERENT_TEAMS

    def test_conservative_more_drivers(
        self, optimizer, predictions_20, team_membership_20
    ):
        cons = optimizer.optimize(
            predictions_20, team_membership_20, "conservative"
        )
        aggr = optimizer.optimize(
            predictions_20, team_membership_20, "aggressive"
        )
        assert len(cons["allocation"]) >= len(aggr["allocation"])

    def test_aggressive_concentrates_tokens(
        self, optimizer, predictions_20, team_membership_20
    ):
        result = optimizer.optimize(
            predictions_20, team_membership_20, "aggressive"
        )
        alloc = result["allocation"]
        max_tokens = max(alloc.values())
        assert max_tokens >= 4  # Aggressive should have high concentration

    def test_all_strategies_valid(
        self, optimizer, predictions_20, team_membership_20
    ):
        for strategy in ["conservative", "balanced", "aggressive", "ultra_aggressive"]:
            result = optimizer.optimize(
                predictions_20, team_membership_20, strategy
            )
            alloc = result["allocation"]
            errors = validate_allocation(alloc, team_membership_20)
            assert errors == [], f"Strategy {strategy} failed: {errors}"

    def test_empty_predictions(self, optimizer):
        result = optimizer.optimize({}, {}, "balanced")
        assert result == {} or result.get("allocation", {}) == {}


# ---------- EV Calculator Tests ----------


class TestEVCalculator:
    def test_driver_ev_positive(self, ev_calc):
        pred = {
            "top10_probability": 0.8,
            "expected_points": 10.0,
            "dnf_probability": 0.05,
        }
        ev = ev_calc.calculate_driver_ev(pred, 3)
        assert ev > 0

    def test_driver_ev_sprint_higher(self, ev_calc):
        pred = {
            "top10_probability": 0.8,
            "expected_points": 10.0,
            "dnf_probability": 0.05,
        }
        ev_normal = ev_calc.calculate_driver_ev(pred, 3, is_sprint=False)
        ev_sprint = ev_calc.calculate_driver_ev(pred, 3, is_sprint=True)
        assert ev_sprint > ev_normal

    def test_correlation_penalty_same_team(self, ev_calc):
        alloc = {"d1": 5, "d2": 5, "d3": 3, "d4": 1, "d5": 1}
        teams = {
            "d1": "t1", "d2": "t1", "d3": "t2", "d4": "t3", "d5": "t4"
        }
        penalty = ev_calc.calculate_correlation_penalty(alloc, teams)
        assert penalty > 0

    def test_no_penalty_different_teams(self, ev_calc):
        alloc = {"d1": 5, "d2": 4, "d3": 3, "d4": 2, "d5": 1}
        teams = {
            "d1": "t1", "d2": "t2", "d3": "t3", "d4": "t4", "d5": "t5"
        }
        penalty = ev_calc.calculate_correlation_penalty(alloc, teams)
        assert penalty == 0.0
