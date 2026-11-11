"""Regression tests for defects found in the system reviews.

Each test names the defect it pins down. Grouped by where the bug lived:
expected value, the allocator, the simulator façade, the collector, and the
seeded season data.
"""

import pytest

from app.collectors.f1_api_fallback import F1ApiFallback
from app.models_ml.monte_carlo import MonteCarloSimulator, SimulationConfig
from app.optimizer.ev_calculator import EVCalculator
from app.optimizer.token_optimizer import TokenOptimizer
from app.utils.validators import BF1_TOTAL_TOKENS, validate_allocation


# --------------------------------------------------------------------------
# Expected value
# --------------------------------------------------------------------------
class TestExpectedValueContract:
    """EV = (chips x E[position points] - penalty x P(retire)) x sprint factor.

    History of this formula: it first multiplied an already-unconditional
    expectation by top10_probability and subtracted the penalty twice; then it
    folded the penalty into the per-chip expectation. Both charged a
    retirement once per *chip*. The pool charges it once per *driver*.
    """

    @pytest.fixture
    def ev_calc(self):
        return EVCalculator()

    def test_chips_scale_points_but_not_the_penalty(self, ev_calc):
        pred = {"expected_points": 10.0, "dnf_probability": 0.2}
        assert ev_calc.calculate_driver_ev(pred, 1) == pytest.approx(10 - 2)
        assert ev_calc.calculate_driver_ev(pred, 5) == pytest.approx(50 - 2)

    def test_retire_probability_takes_precedence_over_dnf(self, ev_calc):
        """A DNS is a non-finish but not a penalised retirement."""
        pred = {"expected_points": 0.0, "dnf_probability": 0.5, "retire_probability": 0.4}
        assert ev_calc.calculate_driver_ev(pred, 1) == pytest.approx(-4.0)

    def test_sprint_multiplier_applied_exactly_once(self, ev_calc):
        pred = {"expected_points": 6.0, "dnf_probability": 0.1}
        normal = ev_calc.calculate_driver_ev(pred, 2, is_sprint=False)
        sprint = ev_calc.calculate_driver_ev(pred, 2, is_sprint=True)
        assert sprint == pytest.approx(normal * 2.0)

    def test_ranking_follows_expected_points(self, ev_calc):
        preds = {
            "leader": {"expected_points": 17.5, "dnf_probability": 0.05},
            "midfield": {"expected_points": 3.2, "dnf_probability": 0.08},
            "backmarker": {"expected_points": 0.3, "dnf_probability": 0.12},
        }
        ranked = sorted(preds, key=lambda d: ev_calc.calculate_driver_ev(preds[d], 1), reverse=True)
        assert ranked == ["leader", "midfield", "backmarker"]

    def test_portfolio_is_the_sum_of_its_drivers(self, ev_calc):
        preds = {"a": {"expected_points": 20, "dnf_probability": 0.1},
                 "b": {"expected_points": 5, "dnf_probability": 0.3}}
        total = ev_calc.calculate_portfolio_ev({"a": 5, "b": 1}, preds)
        assert total == pytest.approx((100 - 1) + (5 - 3))


# --------------------------------------------------------------------------
# Token allocator
# --------------------------------------------------------------------------
def _preds(n):
    return {
        f"d{i}": {"expected_points": max(0.2, 18.0 - i * 0.8), "dnf_probability": 0.05 + i * 0.003}
        for i in range(n)
    }


class TestAllocator:
    @pytest.fixture
    def optimizer(self):
        return TokenOptimizer()

    @pytest.mark.parametrize("n_drivers", [1, 2, 3, 4])
    @pytest.mark.timeout(15)
    def test_small_field_terminates_and_reports(self, optimizer, n_drivers):
        """The old `_fix_total` spun forever when the field was too small to
        reach 15 chips. Now an impossible field is reported, not looped on."""
        teams = {f"d{i}": f"team{i}" for i in range(n_drivers)}
        result = optimizer.optimize(_preds(n_drivers), teams, strategy_type="balanced")
        assert result["allocation"] == {}
        assert result["constraint_errors"], "infeasible allocation reported as valid"

    def test_missing_team_membership_does_not_pass_validation(self, optimizer):
        """Unknown teams must not be counted as distinct teams."""
        result = optimizer.optimize(_preds(20), {}, strategy_type="aggressive")
        assert result["constraint_errors"]

    @pytest.mark.parametrize(
        "style", ["conservative", "balanced", "aggressive", "ultra_aggressive", "nonsense"]
    )
    def test_full_grid_allocation_is_valid(self, optimizer, style):
        """A real 22-driver / 11-team grid must allocate cleanly — and never
        with two drivers from one team, which the pool forbids."""
        teams = {f"d{i}": f"team{i // 2}" for i in range(22)}
        alloc = optimizer.optimize(_preds(22), teams, strategy_type=style)["allocation"]
        assert sum(alloc.values()) == BF1_TOTAL_TOKENS
        assert not validate_allocation(alloc, teams)
        assert len({teams[d] for d in alloc}) == len(alloc)

    def test_default_profile_takes_the_shape_the_rules_reward(self, optimizer):
        """5-5-3-1-1. The old `balanced` default spread 4-3-3-2-2-1 over six
        drivers, which a backtest priced at ~700 points over the 2026 season."""
        teams = {f"d{i}": f"team{i // 2}" for i in range(22)}
        alloc = optimizer.optimize(_preds(22), teams)["allocation"]
        assert sorted(alloc.values(), reverse=True) == [5, 5, 3, 1, 1]

    def test_total_ev_matches_the_calculator(self, optimizer):
        teams = {f"d{i}": f"team{i // 2}" for i in range(22)}
        preds = _preds(22)
        result = optimizer.optimize(preds, teams)
        assert result["total_ev"] == pytest.approx(
            optimizer.ev_calc.calculate_portfolio_ev(result["allocation"], preds)
        )


# --------------------------------------------------------------------------
# Simulator façade
# --------------------------------------------------------------------------
class TestMonteCarlo:
    @pytest.fixture
    def sim(self):
        return MonteCarloSimulator()

    def _grid(self, n, dnf=0.05):
        return [
            {"driver_id": f"d{i}", "grid_position": i + 1, "dnf_probability": dnf, "elo": 1500}
            for i in range(n)
        ]

    def test_position_distribution_covers_full_grid(self, sim):
        """The histogram was hard-coded to 20 slots and dropped P21/P22."""
        n_sims = 500
        results = sim.simulate_race(self._grid(22), {}, {}, SimulationConfig(n_simulations=n_sims))
        for did, r in results.items():
            assert len(r["position_distribution"]) == 22, did
            assert sum(r["position_distribution"]) == n_sims, did

    def test_dnf_probability_cannot_reach_certainty(self, sim):
        """Weather scaling once pushed p past 1.0 and retired the whole field
        in every simulation."""
        results = sim.simulate_race(
            self._grid(20, dnf=0.9), {}, {"weather_risk_index": 1.0},
            SimulationConfig(n_simulations=2000),
        )
        for did, r in results.items():
            assert r["dnf_probability_simulated"] < 0.7, did

    def test_avg_position_uses_unconditional_denominator(self, sim):
        """A driver retiring in half the races was once reported as a P1 car."""
        grid = self._grid(20)
        grid[0]["dnf_probability"] = 0.5
        r = sim.simulate_race(grid, {}, {}, SimulationConfig(n_simulations=4000))["d0"]
        assert r["dnf_probability_simulated"] == pytest.approx(0.5, abs=0.04)
        assert r["avg_position"] > 8.0, f"unreliable pole-sitter reported as P{r['avg_position']}"

    def test_expected_points_exclude_the_penalty(self, sim):
        """Position points only; the flat penalty is applied by the EV layer."""
        grid = self._grid(20)
        grid[19]["dnf_probability"] = 0.5
        results = sim.simulate_race(grid, {}, {}, SimulationConfig(n_simulations=4000))
        assert all(r["expected_points"] >= 0 for r in results.values())
        back = results["d19"]
        assert back["expected_points"] < 1.0
        assert sim.calculate_expected_value(back, 1) < 0      # still a losing pick
        assert sim.calculate_expected_value(back, 0) == 0

    def test_is_fast_enough_for_a_request(self, sim):
        """Vectorised: the old Python loop took seconds and blocked the event loop."""
        import time

        start = time.perf_counter()
        sim.simulate_race(self._grid(22), {}, {}, SimulationConfig(n_simulations=20000))
        assert time.perf_counter() - start < 2.0


# --------------------------------------------------------------------------
# Collector
# --------------------------------------------------------------------------
class TestCollector:
    def test_page_limit_is_explicit(self):
        """Ergast-compatible endpoints default to limit=30 and truncate."""
        collector = F1ApiFallback()
        assert "limit=100" in collector._paged("https://example.test/x.json")
        assert "offset=200" in collector._paged("https://example.test/x.json", 200)

    def test_base_url_is_not_the_decommissioned_host(self):
        assert "ergast.com" not in F1ApiFallback.BASE_URL_ERGAST
        assert F1ApiFallback.BASE_URL_ERGAST.startswith("https://")


# --------------------------------------------------------------------------
# Seeded 2026 season data
# --------------------------------------------------------------------------
class TestSeason2026Data:
    """The seed has twice drifted from the real season: first a 2025-shaped
    grid and a 24-race calendar, then a 22-race one missing the replacement
    round at Sepang. It is now only a starting point — the results sync is the
    authority — but it should still describe the season as actually run."""

    @pytest.fixture
    def seed(self):
        return pytest.importorskip("scripts.seed")

    def test_eleven_teams_twenty_two_drivers(self, seed):
        assert len(seed.GRID) == 11
        assert sum(len(t["drivers"]) for t in seed.GRID) == 22

    def test_new_constructors_present(self, seed):
        names = {t["team"] for t in seed.GRID}
        assert {"Cadillac", "Audi"} <= names and "Kick Sauber" not in names

    def test_identifiers_are_unique_and_present(self, seed):
        drivers = [d for t in seed.GRID for d in t["drivers"]]
        for key in ("number", "code", "ref"):
            values = [d[key] for d in drivers]
            assert all(values) and len(set(values)) == len(values), key
        refs = [t["ref"] for t in seed.GRID]
        assert all(refs) and len(set(refs)) == 11

    def test_calendar_has_23_rounds_numbered_contiguously(self, seed):
        rounds = [gp["round"] for gp in seed.CALENDAR_2026]
        assert rounds == list(range(1, 24))

    def test_replacement_round_at_sepang_is_there(self, seed):
        sepang = [gp for gp in seed.CALENDAR_2026 if gp["circuit_ref"] == "sepang"]
        assert len(sepang) == 1 and sepang[0]["round"] == 16
        refs = {gp["circuit_ref"] for gp in seed.CALENDAR_2026}
        assert "jeddah" not in refs and "bahrain" not in refs

    def test_six_sprint_weekends(self, seed):
        sprints = {gp["circuit_ref"] for gp in seed.CALENDAR_2026 if gp["sprint"]}
        assert sprints == {"shanghai", "miami", "villeneuve", "silverstone", "zandvoort", "marina_bay"}

    def test_venues_are_distinct(self, seed):
        """Circuits are keyed per venue: the USA has three rounds and Spain two."""
        refs = [gp["circuit_ref"] for gp in seed.CALENDAR_2026]
        assert len(set(refs)) == len(refs)

    def test_start_times_are_real_and_chronological(self, seed):
        """Every race was once stamped 14:00 UTC, which put the betting
        deadline hours after the flag for early-morning races."""
        starts = [seed._utc(gp["start"]) for gp in seed.CALENDAR_2026]
        assert starts == sorted(starts)
        assert len({s.hour for s in starts}) > 4
        melbourne = starts[0]
        assert (melbourne.hour, melbourne.tzinfo is not None) == (4, True)
        for gp in seed.CALENDAR_2026:
            if gp["sprint"]:
                assert seed._utc(gp["sprint"]) < seed._utc(gp["start"])
