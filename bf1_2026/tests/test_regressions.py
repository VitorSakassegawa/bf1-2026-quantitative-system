"""Regression tests for defects found in the 2026 system review.

Each test names the defect it pins down. They are grouped by the layer the
bug lived in: expected value, the allocator, the Monte Carlo layer, the
collector, and the seeded season data.
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
    """EV must be tokens * E[points], with the sprint multiplier applied once.

    The old formula was
        tokens * (top10_prob * expected_points * sprint) - dnf_prob * 10
    applied to a value that was *already* the DNF-inclusive, already
    sprint-scaled expectation — so it charged retirements twice, doubled the
    sprint factor, and re-applied a scoring probability that was already
    integrated in. Because the distortion scaled with each driver's own
    probabilities it reordered the field rather than just rescaling it.
    """

    @pytest.fixture
    def ev_calc(self):
        return EVCalculator()

    def test_ev_is_tokens_times_expected_points(self, ev_calc):
        pred = {
            "top10_probability": 0.45,
            "expected_points": 3.2,
            "dnf_probability": 0.08,
        }
        assert ev_calc.calculate_driver_ev(pred, 1) == pytest.approx(3.2)
        assert ev_calc.calculate_driver_ev(pred, 4) == pytest.approx(12.8)

    def test_midfielder_ev_is_not_driven_negative(self, ev_calc):
        """A driver with positive expected points must not score negative EV.

        The game requires at least 5 different teams, so midfield and back
        drivers always have to be picked; the old formula scored them
        negative and ranked them essentially by DNF probability.
        """
        pred = {
            "top10_probability": 0.30,
            "expected_points": 1.4,
            "dnf_probability": 0.09,
        }
        assert ev_calc.calculate_driver_ev(pred, 1) > 0

    def test_sprint_multiplier_applied_exactly_once(self, ev_calc):
        pred = {"top10_probability": 0.9, "expected_points": 10.0, "dnf_probability": 0.05}
        normal = ev_calc.calculate_driver_ev(pred, 2, is_sprint=False)
        sprint = ev_calc.calculate_driver_ev(pred, 2, is_sprint=True)
        assert sprint == pytest.approx(normal * 2.0)

    def test_ranking_follows_expected_points(self, ev_calc):
        """Ordering by EV must reproduce ordering by expected points."""
        preds = {
            "leader": {"top10_probability": 0.95, "expected_points": 17.5, "dnf_probability": 0.05},
            "midfield": {"top10_probability": 0.50, "expected_points": 3.2, "dnf_probability": 0.08},
            "backmarker": {"top10_probability": 0.12, "expected_points": 0.3, "dnf_probability": 0.12},
        }
        ranked = sorted(preds, key=lambda d: ev_calc.calculate_driver_ev(preds[d], 1), reverse=True)
        assert ranked == ["leader", "midfield", "backmarker"]


# --------------------------------------------------------------------------
# Token allocator
# --------------------------------------------------------------------------
class TestAllocatorTermination:
    """_fix_total used to spin forever instead of giving up.

    With fewer than 3 selectable drivers every pick saturates at the 5-token
    cap while the total is still under 15, so a full pass changed nothing and
    the while-loop never exited — a silent 100% CPU spin on the event loop.
    """

    @pytest.fixture
    def optimizer(self):
        return TokenOptimizer()

    @pytest.mark.parametrize("n_drivers", [1, 2, 3, 4])
    @pytest.mark.timeout(15)
    def test_small_field_terminates(self, optimizer, n_drivers):
        preds = {
            f"d{i}": {"top10_probability": 0.5, "expected_points": 5.0, "dnf_probability": 0.05}
            for i in range(n_drivers)
        }
        teams = {f"d{i}": f"team{i}" for i in range(n_drivers)}
        result = optimizer.optimize(preds, teams, strategy_type="balanced")
        assert isinstance(result["allocation"], dict)

    def test_infeasible_field_reports_errors(self, optimizer):
        """An impossible field must surface constraint errors, not hide them."""
        preds = {
            f"d{i}": {"top10_probability": 0.5, "expected_points": 5.0, "dnf_probability": 0.05}
            for i in range(2)
        }
        teams = {f"d{i}": f"team{i}" for i in range(2)}
        result = optimizer.optimize(preds, teams, strategy_type="balanced")
        assert result["constraint_errors"], "infeasible allocation reported as valid"

    def test_missing_team_membership_does_not_pass_validation(self, optimizer):
        """Unknown teams must not be counted as distinct teams.

        _ensure_team_diversity used to invent `unknown_<driver_id>` teams while
        validate_allocation skipped falsy teams, so an allocation covering zero
        real teams satisfied the selector and was returned anyway.
        """
        preds = {
            f"d{i}": {"top10_probability": 0.5, "expected_points": 5.0, "dnf_probability": 0.05}
            for i in range(20)
        }
        result = optimizer.optimize(preds, {}, strategy_type="aggressive")
        assert result["constraint_errors"]

    def test_full_grid_allocation_is_valid(self, optimizer):
        """A realistic 22-driver / 11-team 2026 grid must allocate cleanly."""
        preds, teams = {}, {}
        for i in range(22):
            did = f"d{i}"
            preds[did] = {
                "top10_probability": max(0.05, 0.95 - i * 0.04),
                "expected_points": max(0.2, 18.0 - i * 0.8),
                "dnf_probability": 0.05 + i * 0.003,
            }
            teams[did] = f"team{i // 2}"

        for style in ("conservative", "balanced", "aggressive", "ultra_aggressive"):
            result = optimizer.optimize(preds, teams, strategy_type=style)
            alloc = result["allocation"]
            assert sum(alloc.values()) == BF1_TOTAL_TOKENS, style
            assert not validate_allocation(alloc, teams), style

    def test_total_ev_is_net_of_correlation_penalty(self, optimizer):
        preds, teams = {}, {}
        for i in range(22):
            did = f"d{i}"
            preds[did] = {
                "top10_probability": 0.5,
                "expected_points": 10.0 - i * 0.1,
                "dnf_probability": 0.05,
            }
            teams[did] = f"team{i // 2}"
        result = optimizer.optimize(preds, teams, strategy_type="balanced")
        assert result["total_ev"] == pytest.approx(
            result["gross_ev"] - result["correlation_penalty"]
        )


# --------------------------------------------------------------------------
# Monte Carlo
# --------------------------------------------------------------------------
class TestMonteCarlo:
    @pytest.fixture
    def sim(self):
        return MonteCarloSimulator()

    def _grid(self, n, dnf=0.05):
        return [
            {
                "driver_id": f"d{i}",
                "grid_position": i + 1,
                "dnf_probability": dnf,
                "elo": 1500,
                "wet_performance": 0.0,
            }
            for i in range(n)
        ]

    def test_position_distribution_covers_full_grid(self, sim):
        """The histogram was hard-coded to 20 slots and dropped P21/P22.

        With 22 cars on the 2026 grid the distribution silently stopped
        summing to the simulation count.
        """
        n_sims = 500
        results = sim.simulate_race(
            self._grid(22), {}, {}, SimulationConfig(n_simulations=n_sims)
        )
        for did, r in results.items():
            dist = r["position_distribution"]
            assert len(dist) == 22, did
            assert sum(dist) == n_sims, did

    def test_dnf_probability_cannot_exceed_one(self, sim):
        """weather_risk scaled DNF probability past 1.0 with no clamp.

        p = 0.6 * (1 + 0.8) = 1.08 retired every driver in every simulation.
        """
        results = sim.simulate_race(
            self._grid(20, dnf=0.6),
            {},
            {"weather_risk_index": 0.8},
            SimulationConfig(n_simulations=400),
        )
        for did, r in results.items():
            assert 0.0 <= r["dnf_probability_simulated"] <= 1.0, did
            assert r["dnf_probability_simulated"] < 1.0, f"{did} always retired"

    def test_avg_position_uses_unconditional_denominator(self, sim):
        """avg_position conditioned on finishing while probabilities did not.

        A driver retiring in 90% of sims was reported as a P1.35 car.
        """
        grid = self._grid(20)
        grid[0]["dnf_probability"] = 0.9
        results = sim.simulate_race(grid, {}, {}, SimulationConfig(n_simulations=800))
        r = results["d0"]
        assert r["dnf_probability_simulated"] > 0.8
        assert r["avg_position"] > 3.0, (
            f"unreliable pole-sitter reported as P{r['avg_position']}"
        )

    def test_expected_points_not_pre_scaled_by_sprint(self, sim):
        """The sprint multiplier belongs to EVCalculator alone.

        Applying it here as well made sprint EV 4x instead of 2x.
        """
        cfg = SimulationConfig(n_simulations=400)
        normal = sim.simulate_race(self._grid(20), {}, {}, cfg, is_sprint=False)
        sprint = sim.simulate_race(self._grid(20), {}, {}, cfg, is_sprint=True)
        for did in normal:
            assert normal[did]["expected_bf1_points"] == pytest.approx(
                sprint[did]["expected_bf1_points"]
            )


# --------------------------------------------------------------------------
# Collector
# --------------------------------------------------------------------------
class TestCollectorParsing:
    @pytest.mark.parametrize("status", ["Finished", "+1 Lap", "+2 Laps", "+3 Laps"])
    def test_classified_finishers_are_not_dnf(self, status):
        assert F1ApiFallback._is_dnf(status) is False

    @pytest.mark.parametrize("status", ["+4 Laps", "+5 Laps", "+11 Laps"])
    def test_far_down_finishers_are_not_dnf(self, status):
        """The old allowlist stopped at "+3 Laps", so anyone further down was
        recorded as a retirement *with* a finishing position — corrupting ELO,
        DNF rates, team reliability and the training label at once."""
        assert F1ApiFallback._is_dnf(status) is False

    @pytest.mark.parametrize(
        "status", ["Engine", "Accident", "Collision", "Gearbox", "Disqualified", ""]
    )
    def test_real_retirements_are_dnf(self, status):
        assert F1ApiFallback._is_dnf(status) is True

    def test_page_limit_is_explicit(self):
        """Ergast-compatible endpoints default to limit=30 and truncate."""
        collector = F1ApiFallback()
        assert "limit=" in collector._paged("https://example.test/x.json")
        assert collector.PAGE_LIMIT >= 50

    def test_base_url_is_not_the_decommissioned_host(self):
        assert "ergast.com" not in F1ApiFallback.BASE_URL_ERGAST
        assert F1ApiFallback.BASE_URL_ERGAST.startswith("https://")


# --------------------------------------------------------------------------
# Seeded 2026 season data
# --------------------------------------------------------------------------
class TestSeason2026Data:
    """The seed described a 2025-shaped season: 10 teams, 20 drivers and a
    24-race calendar still containing the cancelled Bahrain and Saudi rounds.
    """

    @pytest.fixture
    def seed(self):
        return pytest.importorskip("scripts.seed")

    def test_eleven_teams_twenty_two_drivers(self, seed):
        assert len(seed.GRID) == 11
        drivers = [d for t in seed.GRID for d in t["drivers"]]
        assert len(drivers) == 22

    def test_new_constructors_present(self, seed):
        names = {t["team"] for t in seed.GRID}
        assert "Cadillac" in names
        assert "Audi" in names
        assert "Kick Sauber" not in names

    def test_driver_numbers_and_codes_unique(self, seed):
        drivers = [d for t in seed.GRID for d in t["drivers"]]
        numbers = [d["number"] for d in drivers]
        codes = [d["code"] for d in drivers]
        assert len(set(numbers)) == len(numbers)
        assert len(set(codes)) == len(codes)

    def test_calendar_has_22_rounds_numbered_contiguously(self, seed):
        rounds = [gp["round"] for gp in seed.CALENDAR_2026]
        assert len(rounds) == 22
        assert rounds == list(range(1, 23))

    def test_cancelled_rounds_removed(self, seed):
        countries = {gp["country"] for gp in seed.CALENDAR_2026}
        assert "Bahrain" not in countries
        assert "Saudi Arabia" not in countries

    def test_six_sprint_rounds(self, seed):
        sprints = {gp["name"] for gp in seed.CALENDAR_2026 if gp["sprint"]}
        assert sprints == {
            "Chinese GP",
            "Miami GP",
            "Canadian GP",
            "British GP",
            "Dutch GP",
            "Singapore GP",
        }

    def test_venues_are_distinct(self, seed):
        """Circuits are keyed per venue, not per country: the USA has three
        rounds and Spain two, which a country-only key collapsed into one."""
        venues = [(gp["city"], gp["country"]) for gp in seed.CALENDAR_2026]
        assert len(set(venues)) == len(venues)

    def test_race_times_are_not_a_single_placeholder(self, seed):
        """Every race was stamped 14:00 UTC, which put the derived betting
        deadline hours after the flag for early races such as Melbourne."""
        times = {gp["time_utc"] for gp in seed.CALENDAR_2026}
        assert len(times) > 1
