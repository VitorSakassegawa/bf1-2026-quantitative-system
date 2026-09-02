"""Guardrails: hard sanity bounds on every model output.

The models (XGBoost, Monte Carlo, KPI heuristics) can, on sparse or unstable
data, produce values outside the physical/regulatory domain of F1 — e.g. a
projection of "5 pit stops", a probability above 1.0, or a finishing position
of 0. Those are not opinions, they are out-of-domain artifacts ("hallucinations").

This module centralizes the clamps so no impossible value ever reaches the user,
and — crucially — *logs a warning whenever a raw value had to be corrected*, so
out-of-domain behavior is detected instead of silently passed through.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from app.utils.validators import BF1_DNF_PENALTY

# --- F1 physical / regulatory bounds -----------------------------------------

# A dry GP essentially always has 1 mandatory stop and, realistically, never
# more than 3. A 4th stop is a genuine outlier seen only in extreme thermal
# degradation, and 5+ is effectively impossible (not enough tyre sets exist).
DRY_MIN_STOPS = 1
DRY_MAX_STOPS = 3
DRY_EXTREME_MAX_STOPS = 4

# On a Sprint weekend teams burn tyre sets in the Sprint Shootout + Sprint, so
# fewer fresh sets remain for the GP -> the realistic ceiling drops.
SPRINT_WEEKEND_MAX_STOPS = 2

# The Sprint race itself is short (~100 km) and is typically a 0/1-stop affair.
SPRINT_RACE_MIN_STOPS = 0
SPRINT_RACE_MAX_STOPS = 1

# Confidence gating: below LOW we refuse to extrapolate beyond the historical
# norm; only at/above HIGH (with extreme degradation) do we permit the rare 4th.
LOW_CONFIDENCE = 0.40
HIGH_CONFIDENCE = 0.70
EXTREME_DEGRADATION = 0.90
DEFAULT_AVG_STOPS = 2.0

# Points bounds: BF1 caps at 25 (win) + 1 (fastest lap); DNF is -10.
MAX_RACE_POINTS = 26.0
SPRINT_POINTS_MULTIPLIER = 2.0

# No single-race retirement is a certainty, and no race retires the whole
# field. Weather scaling (dnf * (1 + weather_risk)) can push a raw probability
# past 1.0, which made every driver retire in every simulation and pinned the
# entire grid at the -10 DNF penalty. Cap it below certainty.
MAX_DNF_PROBABILITY = 0.95


def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp a scalar to [lo, hi]."""
    return max(lo, min(value, hi))


def clamp_probability(p: float) -> float:
    return clamp(float(p), 0.0, 1.0)


def clamp_position(pos: float, grid_size: int = 20) -> float:
    return clamp(float(pos), 1.0, float(grid_size))


def clamp_expected_points(pts: float, is_sprint: bool = False) -> float:
    hi = MAX_RACE_POINTS * (SPRINT_POINTS_MULTIPLIER if is_sprint else 1.0)
    return clamp(float(pts), float(BF1_DNF_PENALTY), hi)


@dataclass
class PitStopProjection:
    projected_stops: int
    label: str           # e.g. "2-stop"
    raw_estimate: float  # what the model computed before guardrails
    capped: bool         # True if guardrails reduced an out-of-domain value
    note: str | None     # human-readable explanation when capped/anchored
    confidence: float


class PitStopProjector:
    """Translate a degradation signal into a *bounded* pit-stop projection.

    A raw degradation estimate is mapped to a continuous stop count, then forced
    into the realistic window for the session context (dry GP / Sprint weekend /
    Sprint race) and gated by confidence. The result is always physically and
    regulation-plausible.
    """

    def project(
        self,
        tire_degradation: float | None = None,
        raw_stops_estimate: float | None = None,
        historical_avg_stops: float | None = None,
        confidence: float = 0.6,
        is_sprint_weekend: bool = False,
        is_sprint_race: bool = False,
    ) -> PitStopProjection:
        confidence = clamp_probability(confidence)
        anchor = historical_avg_stops if historical_avg_stops else DEFAULT_AVG_STOPS

        # 1) Context window
        if is_sprint_race:
            lo, hi = SPRINT_RACE_MIN_STOPS, SPRINT_RACE_MAX_STOPS
        elif is_sprint_weekend:
            lo, hi = DRY_MIN_STOPS, SPRINT_WEEKEND_MAX_STOPS
        else:
            lo, hi = DRY_MIN_STOPS, DRY_MAX_STOPS

        # 2) Raw estimate
        if raw_stops_estimate is not None:
            raw = float(raw_stops_estimate)
        elif tire_degradation is not None:
            raw = 1.0 + clamp(float(tire_degradation), 0.0, 1.0) * 3.0
        else:
            raw = anchor
        raw_pre = raw

        # 3) Confidence anchoring: when unconfident, don't stray from the norm.
        note: str | None = None
        if confidence < LOW_CONFIDENCE:
            anchored = clamp(raw, anchor - 1.0, anchor + 1.0)
            if abs(anchored - raw) > 1e-9:
                note = (
                    f"Low confidence ({confidence:.0%}): anchored to historical "
                    f"~{anchor:.0f}-stop norm instead of extrapolating."
                )
            raw = anchored

        # 4) Rare-extreme allowance (only with high confidence AND extreme deg).
        eff_hi = hi
        deg = tire_degradation or 0.0
        if (
            not is_sprint_race
            and confidence >= HIGH_CONFIDENCE
            and deg >= EXTREME_DEGRADATION
        ):
            eff_hi = min(hi + 1, DRY_EXTREME_MAX_STOPS)

        # 5) Round & hard-clamp.
        final = int(round(clamp(raw, lo, eff_hi)))

        # 6) Was an out-of-domain value corrected?
        capped = int(round(raw_pre)) > final
        if capped and note is None:
            ctx = (
                "Sprint race"
                if is_sprint_race
                else "Sprint weekend (limited fresh tyre sets)"
                if is_sprint_weekend
                else "dry GP"
            )
            note = (
                f"Model raw estimate ~{raw_pre:.0f} stops is not physically "
                f"realistic for a {ctx}; capped to {final}. This signals critical "
                f"tyre degradation (a tyre-management race), not {raw_pre:.0f} actual stops."
            )
            logger.warning(
                f"PitStopProjector capped raw={raw_pre:.1f} -> {final} "
                f"(sprint_weekend={is_sprint_weekend}, sprint_race={is_sprint_race}, "
                f"conf={confidence:.2f}, deg={deg:.2f})"
            )

        return PitStopProjection(
            projected_stops=final,
            label=f"{final}-stop",
            raw_estimate=round(raw_pre, 2),
            capped=capped,
            note=note,
            confidence=confidence,
        )


def validate_prediction(
    pred: dict,
    grid_size: int = 20,
    is_sprint: bool = False,
    context: str = "",
) -> dict:
    """Clamp a per-driver prediction dict in place and log any corrections.

    Guards: probabilities in [0,1], top3 <= top10 (monotonic), position in
    [1, grid_size], expected_points within BF1 bounds.
    """
    issues: list[str] = []
    out = dict(pred)

    for key in ("top3_probability", "top10_probability", "dnf_probability"):
        if key in out:
            raw = float(out[key])
            fixed = clamp_probability(raw)
            if abs(fixed - raw) > 1e-9:
                issues.append(f"{key}={raw:.3f}->{fixed:.3f}")
            out[key] = round(fixed, 4)

    # Monotonicity: P(top3) can never exceed P(top10).
    if "top3_probability" in out and "top10_probability" in out:
        if out["top3_probability"] > out["top10_probability"]:
            issues.append(
                f"top3({out['top3_probability']}) > top10({out['top10_probability']})"
            )
            out["top3_probability"] = out["top10_probability"]

    if "expected_position" in out:
        raw = float(out["expected_position"])
        fixed = clamp_position(raw, grid_size)
        if abs(fixed - raw) > 1e-9:
            issues.append(f"expected_position={raw:.2f}->{fixed:.2f}")
        out["expected_position"] = round(fixed, 2)

    if "expected_points" in out:
        raw = float(out["expected_points"])
        fixed = clamp_expected_points(raw, is_sprint)
        if abs(fixed - raw) > 1e-9:
            issues.append(f"expected_points={raw:.2f}->{fixed:.2f}")
        out["expected_points"] = round(fixed, 2)

    if issues:
        logger.warning(
            f"Guardrails corrected prediction "
            f"{('[' + context + '] ') if context else ''}: {', '.join(issues)}"
        )

    return out
