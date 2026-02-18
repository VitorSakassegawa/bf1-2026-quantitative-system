"""Tests for the ELO rating system."""

import pytest

from app.elo.elo_engine import ELOEngine


@pytest.fixture
def engine():
    return ELOEngine()


@pytest.fixture
def race_results():
    """20 drivers finishing in grid order (no upsets)."""
    return [
        {"driver_id": f"d{i}", "final_position": i, "dnf": False}
        for i in range(1, 21)
    ]


@pytest.fixture
def equal_elos():
    """All drivers start at 1500."""
    return {f"d{i}": 1500.0 for i in range(1, 21)}


class TestELO:
    def test_expected_score_equal(self, engine):
        """Equal ELOs → expected score = 0.5."""
        score = engine.calculate_expected_score(1500, 1500)
        assert score == pytest.approx(0.5)

    def test_expected_score_higher_elo(self, engine):
        """Higher ELO → expected score > 0.5."""
        score = engine.calculate_expected_score(1700, 1500)
        assert score > 0.5

    def test_expected_score_lower_elo(self, engine):
        """Lower ELO → expected score < 0.5."""
        score = engine.calculate_expected_score(1300, 1500)
        assert score < 0.5

    def test_update_returns_all_drivers(self, engine, race_results, equal_elos):
        updates = engine.update_elo_race(race_results, equal_elos)
        assert len(updates) == 20

    def test_winner_gains_elo(self, engine, race_results, equal_elos):
        updates = engine.update_elo_race(race_results, equal_elos)
        winner = updates["d1"]
        assert winner["elo_after"] > winner["elo_before"]
        assert winner["elo_delta"] > 0

    def test_last_place_loses_elo(self, engine, race_results, equal_elos):
        updates = engine.update_elo_race(race_results, equal_elos)
        last = updates["d20"]
        assert last["elo_after"] < last["elo_before"]
        assert last["elo_delta"] < 0

    def test_elo_changes_sum_near_zero(self, engine, race_results, equal_elos):
        """Total ELO gains ≈ total ELO losses (zero-sum)."""
        updates = engine.update_elo_race(race_results, equal_elos)
        total_delta = sum(u["elo_delta"] for u in updates.values())
        assert abs(total_delta) < 1.0  # near-zero due to float precision

    def test_sprint_lower_impact(self, engine, race_results, equal_elos):
        """Sprint should have smaller ELO changes than a full race."""
        race_updates = engine.update_elo_race(race_results, equal_elos)
        sprint_updates = engine.update_elo_sprint(race_results, equal_elos)

        race_delta_sum = sum(abs(u["elo_delta"]) for u in race_updates.values())
        sprint_delta_sum = sum(
            abs(u["elo_delta"]) for u in sprint_updates.values()
        )
        assert sprint_delta_sum < race_delta_sum

    def test_dnf_treated_as_last(self, engine, equal_elos):
        results = [
            {"driver_id": "d1", "final_position": 1, "dnf": False},
            {"driver_id": "d2", "final_position": None, "dnf": True},
            {"driver_id": "d3", "final_position": 2, "dnf": False},
        ]
        updates = engine.update_elo_race(results, equal_elos)
        # DNF driver should lose ELO
        assert updates["d2"]["elo_delta"] < 0

    def test_convergence_over_races(self, engine):
        """After many races, strong driver should have higher ELO."""
        elos = {f"d{i}": 1500.0 for i in range(1, 6)}

        # Driver d1 always wins, d5 always last
        for _ in range(20):
            results = [
                {"driver_id": f"d{i}", "final_position": i, "dnf": False}
                for i in range(1, 6)
            ]
            updates = engine.update_elo_race(results, elos)
            for did, update in updates.items():
                elos[did] = update["elo_after"]

        assert elos["d1"] > elos["d5"]
        assert elos["d1"] > 1500
        assert elos["d5"] < 1500

    def test_team_elo(self, engine):
        team_results = {
            "team_1": [1, 3],  # avg = 2
            "team_2": [10, 12],  # avg = 11
        }
        updates = engine.update_team_elo(team_results)
        assert updates["team_1"]["elo_delta"] > 0
        assert updates["team_2"]["elo_delta"] < 0
