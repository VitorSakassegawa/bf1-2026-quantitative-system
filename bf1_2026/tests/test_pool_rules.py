"""The pool's scoring rules, checked against bets the pool itself scored.

Each `REAL_BETS` case is a bet actually placed in the 2026 pool together with
the score the pool's site reports for it. If the scorer reproduces those, it
implements the rules as the pool applies them — not as a README describes them.
"""

import pytest

from app.rules.pool_rules import DEFAULT_RULES, PoolRules, ResultLine
from app.utils.validators import (
    BF1_DNF_PENALTY,
    BF1_POINTS_TABLE,
    calculate_bf1_points,
    validate_allocation,
)

R = DEFAULT_RULES


def line(position=None, retired=False):
    return ResultLine(position=position, retired=retired)


# (label, allocation, 11th pick, results, sprint, automatic penalty, pool score)
REAL_BETS = [
    (
        "United States GP — 11th place hit",
        {"ANT": 5, "VER": 5, "HAM": 3, "PIA": 1, "LIN": 1},
        "HUL",
        {"ANT": line(1), "VER": line(3), "HAM": line(4), "PIA": line(7),
         "LIN": line(15), "HUL": line(11)},
        False, False, 292.0,
    ),
    (
        "Dutch GP — hit, plus one retirement and one finisher out of the points",
        {"NOR": 5, "ANT": 5, "HAM": 3, "VER": 1, "BOR": 1},
        "TSU",
        {"NOR": line(1), "ANT": line(2), "HAM": line(4), "VER": line(retired=True),
         "BOR": line(14), "TSU": line(11)},
        False, False, 291.0,
    ),
    (
        "Australian GP — a DNS scores nothing but is NOT penalised",
        {"LEC": 4, "RUS": 4, "PIA": 3, "HAD": 2, "LAW": 2},
        "SAI",
        {"RUS": line(1), "LEC": line(3), "PIA": line(), "HAD": line(retired=True),
         "LAW": line(13), "OCO": line(11)},
        False, False, 150.0,
    ),
    (
        "Chinese sprint — sprint table, score doubled",
        {"RUS": 5, "HAM": 3, "NOR": 5, "VER": 1, "GAS": 1},
        "OCO",
        {"RUS": line(1), "HAM": line(3), "NOR": line(4), "VER": line(9), "GAS": line(11)},
        True, False, 166.0,
    ),
    (
        "Singapore sprint — second automatic bet, 20 % off",
        {"HAM": 3, "VER": 5, "ANT": 5, "NOR": 1, "GAS": 1},
        "BOR",
        {"VER": line(1), "HAM": line(2), "ANT": line(4), "NOR": line(8),
         "GAS": line(9), "ALO": line(11)},
        True, True, 139.2,
    ),
]


@pytest.mark.parametrize(
    "label,alloc,pick,results,sprint,auto,expected", REAL_BETS, ids=[c[0] for c in REAL_BETS]
)
def test_scorer_reproduces_the_pools_own_scores(label, alloc, pick, results, sprint, auto, expected):
    got = R.score(alloc, pick, results, sprint=sprint, automatic_penalty=auto)
    assert got.total == pytest.approx(expected)


class TestScoringRules:
    def test_points_table(self):
        assert [R.points_for(p) for p in range(1, 12)] == [25, 18, 15, 12, 10, 8, 6, 4, 2, 1, 0]
        assert [R.points_for(p, sprint=True) for p in range(1, 10)] == [8, 7, 6, 5, 4, 3, 2, 1, 0]
        assert R.points_for(None) == 0

    def test_retirement_penalty_is_flat_per_driver(self):
        """-10 whether the driver had one chip or five."""
        results = {"A": line(retired=True)}
        assert R.score({"A": 1}, None, results).total == -10
        assert R.score({"A": 5}, None, results).total == -10

    def test_eleventh_bonus_needs_a_classified_eleventh(self):
        assert R.score({}, "X", {"X": line(11)}).total == 50
        assert R.score({}, "X", {"X": line(10)}).total == 0
        assert R.score({}, None, {"X": line(11)}).total == 0

    def test_sprint_doubles_points_bonus_and_penalty(self):
        results = {"A": line(1), "B": line(retired=True), "X": line(11)}
        got = R.score({"A": 5, "B": 1}, "X", results, sprint=True)
        assert got.total == (5 * 8 + 50 - 10) * 2
        assert got.eleventh_bonus == 100

    def test_maximum_matches_what_the_pool_publishes(self):
        """The pool's `maximum_points`: 332 for a race, 304 for a sprint."""
        assert R.max_score(sprint=False) == 332
        assert R.max_score(sprint=True) == 304
        assert R.ideal_chip_pattern() == [5, 5, 3, 1, 1]

    def test_legacy_helpers_follow_the_rules(self):
        assert BF1_DNF_PENALTY == -10
        assert BF1_POINTS_TABLE[1] == 25 and BF1_POINTS_TABLE[10] == 1
        assert calculate_bf1_points(1, False, chips=5) == 125
        assert calculate_bf1_points(3, True, chips=5) == -10
        assert calculate_bf1_points(1, False, chips=2, sprint=True) == 32


class TestValidation:
    TEAMS = {f"d{i}": f"t{i}" for i in range(1, 8)}

    def test_ideal_bet_is_valid(self):
        assert R.validate({"d1": 5, "d2": 5, "d3": 3, "d4": 1, "d5": 1}, self.TEAMS) == []

    def test_two_drivers_of_one_team_are_rejected(self):
        """The pool's `same_team_allowed: false`.

        The old validator only wanted five *distinct* teams, so a sixth driver
        could repeat one — a bet the pool's own form refuses.
        """
        teams = {**self.TEAMS, "d6": "t1"}
        errors = R.validate({"d1": 5, "d2": 4, "d3": 2, "d4": 2, "d5": 1, "d6": 1}, teams)
        assert any("one driver per team" in e for e in errors)
        assert validate_allocation({"d1": 5, "d2": 4, "d3": 2, "d4": 2, "d5": 1, "d6": 1}, teams)

    @pytest.mark.parametrize(
        "alloc",
        [
            {"d1": 5, "d2": 5, "d3": 3, "d4": 1},                    # 4 drivers, 14 chips
            {"d1": 6, "d2": 5, "d3": 2, "d4": 1, "d5": 1},           # 6 on one driver
            {"d1": 5, "d2": 5, "d3": 3, "d4": 1, "d5": 1, "d6": 1},  # 16 chips
            {"d1": 5, "d2": 5, "d3": 4, "d4": 1, "d5": 0},           # a zero-chip pick
        ],
    )
    def test_invalid_bets_are_rejected(self, alloc):
        assert R.validate(alloc, self.TEAMS)

    def test_unknown_team_cannot_satisfy_the_rules(self):
        assert R.validate({"d1": 5, "d2": 5, "d3": 3, "d4": 1, "zz": 1}, self.TEAMS)


class TestFromPoolPayload:
    # The two rule objects the pool's API actually returns.
    CURRENT_RULES = {
        "name": "2026", "race_type": "Normal", "total_chips": 15,
        "max_chips_per_driver": 5, "minimum_drivers": 5, "allow_same_team": False,
        "discard_enabled": True, "eleventh_bonus": 50,
        "retirement_penalty_enabled": True, "retirement_penalty_points": 10,
        "automatic_bet_penalty_percent": 20.0, "doubled_sprint_points": True,
        "position_points": [25, 18, 15, 12, 10, 8, 6, 4, 2, 1] + [0] * 10,
    }
    FORM_RULES = {
        "total_chips": 15, "max_chips_per_driver": 5, "minimum_drivers": 5,
        "same_team_allowed": False,
    }

    def test_live_payload_equals_the_engine_defaults(self):
        assert PoolRules.from_pool_payload(self.CURRENT_RULES) == DEFAULT_RULES

    def test_form_payload_spelling_is_understood(self):
        assert PoolRules.from_pool_payload(self.FORM_RULES) == DEFAULT_RULES
        assert PoolRules.from_pool_payload({"same_team_allowed": True}).allow_same_team

    def test_changed_rules_are_picked_up(self):
        payload = {**self.CURRENT_RULES, "eleventh_bonus": 80, "total_chips": 20,
                   "retirement_penalty_enabled": False}
        rules = PoolRules.from_pool_payload(payload)
        assert rules.eleventh_bonus == 80
        assert rules.total_chips == 20
        assert rules.retirement_penalty == 0

    def test_sprint_payload_sets_the_sprint_table(self):
        payload = {"race_type": "Sprint", "position_points": [10, 8, 6, 0, 0]}
        rules = PoolRules.from_pool_payload(payload)
        assert rules.sprint_position_points == (10, 8, 6)
        assert rules.position_points == DEFAULT_RULES.position_points

    @pytest.mark.parametrize("payload", [None, {}, {"total_chips": "abc", "position_points": []}])
    def test_empty_or_garbled_payload_falls_back_to_defaults(self, payload):
        assert PoolRules.from_pool_payload(payload) == DEFAULT_RULES
