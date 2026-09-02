"""Tests for regulation-era scoping, the KPI window, and ELO's influence.

Two modelling defects sit behind these:

* `window_years=5` was stored by both KPI calculators and never referenced, so
  every "5-year" KPI actually pooled all history — across the 2022 ground-effect
  change and the 2026 chassis/power-unit reset.
* ELO contributed ~0.001 positions per rating point against ~2.5 of simulation
  noise, so the rating system had no measurable effect on any strategy.
"""

import pytest

from app.models_ml.monte_carlo import (
    ELO_BASELINE,
    MonteCarloSimulator,
    SimulationConfig,
)
from app.utils.season_window import (
    DEFAULT_WINDOW_YEARS,
    MIN_ROWS_FOR_ERA_ONLY,
    crosses_era_boundary,
    era_start_for,
    regress_elo_for_new_era,
    same_era,
    seasons_in_scope,
    window_start_season,
)


class TestRegulationEras:
    @pytest.mark.parametrize(
        "season,expected",
        [
            (2015, 2014),
            (2018, 2017),
            (2021, 2017),
            (2022, 2022),
            (2025, 2022),
            (2026, 2026),
            (2027, 2026),
        ],
    )
    def test_era_boundaries(self, season, expected):
        assert era_start_for(season) == expected

    def test_2026_is_its_own_era(self):
        """New chassis and power-unit rules, an eleventh team on the grid."""
        assert not same_era(2025, 2026)
        assert crosses_era_boundary(2025, 2026)

    def test_ordinary_season_change_is_not_a_boundary(self):
        assert not crosses_era_boundary(2024, 2025)
        assert not crosses_era_boundary(2022, 2023)


class TestEloEraRegression:
    def test_ratings_move_toward_the_baseline(self):
        assert regress_elo_for_new_era(1750, ELO_BASELINE) == pytest.approx(1625)
        assert regress_elo_for_new_era(1300, ELO_BASELINE) == pytest.approx(1400)

    def test_baseline_rating_is_unchanged(self):
        assert regress_elo_for_new_era(ELO_BASELINE, ELO_BASELINE) == pytest.approx(
            ELO_BASELINE
        )

    def test_ordering_is_preserved(self):
        """Skill carries over even though the machinery order does not."""
        a = regress_elo_for_new_era(1700, ELO_BASELINE)
        b = regress_elo_for_new_era(1600, ELO_BASELINE)
        c = regress_elo_for_new_era(1400, ELO_BASELINE)
        assert a > b > c

    @pytest.mark.parametrize("regression,expected", [(0.0, 1700), (1.0, 1500)])
    def test_regression_bounds(self, regression, expected):
        got = regress_elo_for_new_era(1700, ELO_BASELINE, regression)
        assert got == pytest.approx(expected)


class TestSeasonScoping:
    def test_window_start_is_inclusive_and_five_seasons_wide(self):
        assert window_start_season(DEFAULT_WINDOW_YEARS, season=2026) == 2022

    def test_current_era_is_preferred_when_it_has_enough_data(self):
        seasons = [2021, 2022, 2023, 2024, 2025, 2026]
        rows = {s: 40 for s in seasons}
        scope = seasons_in_scope(
            seasons, season=2026, min_rows=MIN_ROWS_FOR_ERA_ONLY, rows_per_season=rows
        )
        assert scope == [2026]

    def test_falls_back_to_the_window_when_the_era_is_thin(self):
        """Early in a reset season there is not yet enough data to stand alone."""
        seasons = [2020, 2021, 2022, 2023, 2024, 2025, 2026]
        rows = {s: 40 for s in seasons}
        rows[2026] = 2
        scope = seasons_in_scope(
            seasons, season=2026, min_rows=MIN_ROWS_FOR_ERA_ONLY, rows_per_season=rows
        )
        assert scope == [2022, 2023, 2024, 2025, 2026]
        assert 2020 not in scope and 2021 not in scope

    def test_pre_reset_seasons_are_dropped_once_the_era_stands_alone(self):
        seasons = [2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026]
        rows = {s: 40 for s in seasons}
        scope = seasons_in_scope(
            seasons, season=2026, min_rows=MIN_ROWS_FOR_ERA_ONLY, rows_per_season=rows
        )
        assert all(s >= 2026 for s in scope)

    def test_empty_input_is_handled(self):
        assert seasons_in_scope([], season=2026) == []

    def test_never_returns_nothing_when_data_exists(self):
        """A brand-new era must still yield a number rather than a null KPI."""
        scope = seasons_in_scope(
            [2013, 2014], season=2026, min_rows=99, rows_per_season={2013: 1, 2014: 1}
        )
        assert scope


class TestEloActuallyMovesTheSimulation:
    """The calibration target: ELO expressed in position units.

    `elo_points_per_position` says how much rating advantage is worth one
    position of race pace. The grid already carries qualifying pace, so this
    term only carries what ELO measures — race craft.
    """

    @pytest.fixture
    def sim(self):
        return MonteCarloSimulator()

    def _avg_at_p10(self, sim, elo, n=6000):
        drivers = [
            {
                "driver_id": f"d{i}",
                "grid_position": i + 1,
                "dnf_probability": 0.05,
                "elo": elo if i == 9 else ELO_BASELINE,
                "wet_performance": 0.0,
            }
            for i in range(20)
        ]
        cfg = SimulationConfig(n_simulations=n)
        return sim.simulate_race(drivers, {}, {}, cfg)["d9"]["avg_position"]

    def test_higher_elo_finishes_ahead(self, sim):
        strong = self._avg_at_p10(sim, ELO_BASELINE + 200)
        weak = self._avg_at_p10(sim, ELO_BASELINE - 200)
        assert strong < weak

    def test_effect_is_material_not_rounding_noise(self, sim):
        """A 200-point edge must be worth more than a position."""
        strong = self._avg_at_p10(sim, ELO_BASELINE + 200)
        weak = self._avg_at_p10(sim, ELO_BASELINE - 200)
        assert (weak - strong) > 1.0

    def test_effect_is_roughly_symmetric(self, sim):
        base = self._avg_at_p10(sim, ELO_BASELINE)
        up = base - self._avg_at_p10(sim, ELO_BASELINE + 150)
        down = self._avg_at_p10(sim, ELO_BASELINE - 150) - base
        assert up == pytest.approx(down, abs=0.5)

    def test_grid_position_still_dominates(self, sim):
        """Sanity guard: ELO must not overwhelm where a driver starts."""
        drivers = [
            {
                "driver_id": f"d{i}",
                "grid_position": i + 1,
                "dnf_probability": 0.05,
                # Ratings run backwards against the grid.
                "elo": ELO_BASELINE - 100 + i * 10,
                "wet_performance": 0.0,
            }
            for i in range(20)
        ]
        res = sim.simulate_race(drivers, {}, {}, SimulationConfig(n_simulations=3000))
        assert res["d0"]["avg_position"] < res["d19"]["avg_position"]
