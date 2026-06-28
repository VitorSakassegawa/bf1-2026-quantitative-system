"""Tests for the anti-hallucination guardrails.

These lock in the behavior that prevents out-of-domain outputs like the
"5 pit stops" projection that prompted this module.
"""

import pytest

from app.utils.guardrails import (
    DRY_MAX_STOPS,
    SPRINT_WEEKEND_MAX_STOPS,
    PitStopProjector,
    clamp_expected_points,
    clamp_position,
    clamp_probability,
    validate_prediction,
)


@pytest.fixture
def projector():
    return PitStopProjector()


# ---------- Pit-stop projection (the core bug) ----------


class TestPitStopProjector:
    def test_five_stops_is_impossible_on_dry_gp(self, projector):
        """A raw 5-stop estimate must never survive on a normal GP."""
        proj = projector.project(raw_stops_estimate=5, confidence=0.9)
        assert proj.projected_stops <= DRY_MAX_STOPS
        assert proj.capped is True
        assert proj.note is not None
        assert "5" in proj.note  # explains the raw value to the user

    def test_sprint_weekend_canada_caps_lower(self, projector):
        """Canada-style Sprint weekend: limited tyre sets -> max 2 (the reported bug)."""
        proj = projector.project(
            raw_stops_estimate=5, confidence=0.5, is_sprint_weekend=True
        )
        assert proj.projected_stops <= SPRINT_WEEKEND_MAX_STOPS
        assert proj.capped is True

    def test_austria_three_to_two_when_trend_is_two(self, projector):
        """Austria: a 3-stop raw on a sprint weekend with modest confidence -> 2."""
        proj = projector.project(
            raw_stops_estimate=3,
            confidence=0.5,
            is_sprint_weekend=True,
        )
        assert proj.projected_stops == 2

    def test_sprint_race_is_zero_or_one(self, projector):
        proj = projector.project(raw_stops_estimate=3, is_sprint_race=True)
        assert proj.projected_stops <= 1

    def test_low_confidence_anchors_to_history(self, projector):
        """Unstable data (low confidence) must not extrapolate beyond the norm."""
        proj = projector.project(
            raw_stops_estimate=4,
            historical_avg_stops=2.0,
            confidence=0.2,  # below LOW_CONFIDENCE
        )
        assert proj.projected_stops <= 3
        assert proj.note is not None

    def test_extreme_4th_stop_only_with_high_confidence(self, projector):
        """A 4th stop is allowed *only* with high confidence + extreme degradation."""
        allowed = projector.project(
            tire_degradation=0.98, raw_stops_estimate=4, confidence=0.85
        )
        assert allowed.projected_stops == 4

        denied = projector.project(
            tire_degradation=0.5, raw_stops_estimate=4, confidence=0.85
        )
        assert denied.projected_stops <= DRY_MAX_STOPS

    def test_normal_two_stop_passes_through(self, projector):
        proj = projector.project(raw_stops_estimate=2, confidence=0.8)
        assert proj.projected_stops == 2
        assert proj.capped is False
        assert proj.label == "2-stop"

    def test_degradation_signal_maps_into_range(self, projector):
        """Even maximum degradation stays within the realistic window."""
        proj = projector.project(tire_degradation=1.0, confidence=0.5)
        assert 1 <= proj.projected_stops <= DRY_MAX_STOPS


# ---------- Scalar clamps ----------


class TestClamps:
    def test_probability_clamped(self):
        assert clamp_probability(1.4) == 1.0
        assert clamp_probability(-0.2) == 0.0
        assert clamp_probability(0.5) == 0.5

    def test_position_clamped(self):
        assert clamp_position(0) == 1.0
        assert clamp_position(25, grid_size=20) == 20.0

    def test_expected_points_bounds(self):
        assert clamp_expected_points(999) == 26.0
        assert clamp_expected_points(-50) == -10.0
        # Sprint doubles the ceiling
        assert clamp_expected_points(999, is_sprint=True) == 52.0


# ---------- Prediction validation ----------


class TestValidatePrediction:
    def test_out_of_range_probabilities_fixed(self):
        pred = {
            "top3_probability": 1.5,
            "top10_probability": 2.0,
            "dnf_probability": -0.1,
            "expected_position": 0,
            "expected_points": 500,
        }
        out = validate_prediction(pred)
        assert 0 <= out["top3_probability"] <= 1
        assert 0 <= out["top10_probability"] <= 1
        assert out["dnf_probability"] == 0.0
        assert out["expected_position"] >= 1
        assert out["expected_points"] <= 26.0

    def test_monotonicity_top3_le_top10(self):
        """P(top3) can never exceed P(top10)."""
        pred = {"top3_probability": 0.9, "top10_probability": 0.4}
        out = validate_prediction(pred)
        assert out["top3_probability"] <= out["top10_probability"]

    def test_valid_prediction_unchanged(self):
        pred = {
            "top3_probability": 0.3,
            "top10_probability": 0.7,
            "dnf_probability": 0.05,
            "expected_position": 4.0,
            "expected_points": 12.0,
        }
        out = validate_prediction(pred)
        assert out["top3_probability"] == 0.3
        assert out["expected_position"] == 4.0
