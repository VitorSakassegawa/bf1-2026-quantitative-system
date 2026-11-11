"""The race simulator, the form book and the exact optimiser."""

import itertools

import numpy as np
import pytest

from app.models_ml.backtest import Event, EventRow, walk_forward
from app.models_ml.race_model import (
    DEFAULT_PARAMS,
    ELO_BASELINE,
    Entry,
    FormBook,
    ObservedResult,
    simulate,
)
from app.optimizer.pool_optimizer import (
    RISK_PROFILES,
    exact_ev_allocation,
    optimize_allocation,
    pick_eleventh,
)
from app.optimizer.strategy_engine import StrategyEngine
from app.rules.pool_rules import DEFAULT_RULES as R

TEAMS = ["mer", "fer", "mcl", "rbr", "ast", "alp", "wil", "rb", "haas", "audi", "cad"]


def grid(n=22, dnf=0.1):
    return [
        Entry(f"d{i:02d}", TEAMS[i // 2], i + 1, ELO_BASELINE, float(i + 1), dnf)
        for i in range(n)
    ]


def team_of(entries):
    return {e.driver_id: e.team_id for e in entries}


class TestSimulation:
    def test_probabilities_are_coherent(self):
        sim = simulate(grid(), n_simulations=4000)
        assert np.all(sim.top3 <= sim.top10 + 1e-9)
        assert np.all((0 <= sim.dnf_probability) & (sim.dnf_probability <= 1))
        # One winner, three podium places, one 11th place per simulated race.
        assert sim.win.sum() == pytest.approx(1.0, abs=0.01)
        assert sim.top3.sum() == pytest.approx(3.0, abs=0.02)
        assert sim.p11.sum() == pytest.approx(1.0, abs=0.02)

    def test_positions_are_a_permutation_with_retirements_last(self):
        sim = simulate(grid(), n_simulations=500)
        assert np.all(np.sort(sim.positions, axis=1) == np.arange(1, 23))
        for row in range(0, 500, 50):
            out = sim.out[row]
            if out.any() and (~out).any():
                assert sim.positions[row][out].min() > sim.positions[row][~out].max()

    def test_non_finishers_score_nothing(self):
        sim = simulate(grid(dnf=0.5), n_simulations=1000)
        assert np.all(sim.points[sim.out] == 0)
        assert np.all(sim.retired <= sim.out)       # a penalty implies not classified

    def test_pole_beats_the_back_of_the_grid(self):
        sim = simulate(grid(), n_simulations=4000)
        assert sim.expected_points[0] > sim.expected_points[5] > sim.expected_points[15]
        assert sim.win[0] > 0.25

    def test_spread_grows_towards_the_back(self):
        """Real results: finish-minus-grid spreads ~2.8 places at the front and
        ~4.5 at the back. The simulated spread must grow the same way."""
        entries = grid(dnf=0.2)
        sim = simulate(entries, n_simulations=8000)

        def spread(lo, hi):
            deltas = [
                sim.positions[~sim.out[:, g - 1], g - 1] - g for g in range(lo, hi + 1)
            ]
            return np.concatenate(deltas).std()

        assert spread(1, 3) < spread(7, 10) < spread(16, 22)
        # Cars at the back gain places as others retire ahead of them.
        back = sim.positions[~sim.out[:, 19], 19].mean()
        assert back < 20

    def test_a_front_runner_can_have_a_bad_day(self):
        """Incidents give the pole-sitter a real chance of finishing outside
        the points while still classified — Gaussian noise alone gives ~0."""
        sim = simulate(grid(dnf=0.0), n_simulations=20000)
        outside_points = (sim.positions[:, 0] > 10).mean()
        assert 0.002 < outside_points < 0.08

    def test_reliability_drives_dnf_probability(self):
        entries = grid()
        entries[0].dnf_probability = 0.5
        sim = simulate(entries, n_simulations=6000)
        assert sim.dnf_probability[0] == pytest.approx(0.5, abs=0.03)
        assert sim.dnf_probability[1] == pytest.approx(0.1, abs=0.02)

    def test_dnf_probability_is_capped_below_certainty(self):
        entries = grid(dnf=0.99)
        sim = simulate(entries, n_simulations=20000, weather_risk=1.0)
        # Capped at max_dnf_probability; the mean over drivers removes the
        # sampling noise a single driver's estimate carries.
        assert sim.dnf_probability.mean() == pytest.approx(
            DEFAULT_PARAMS.max_dnf_probability, abs=0.01
        )

    def test_sprint_uses_its_own_points_and_fewer_retirements(self):
        race = simulate(grid(), n_simulations=6000, sprint=False)
        sprint = simulate(grid(), n_simulations=6000, sprint=True)
        assert sprint.points.max() == 8 and race.points.max() == 25
        assert sprint.dnf_probability.mean() < race.dnf_probability.mean() * 0.75

    def test_better_form_and_rating_help(self):
        base = simulate(grid(dnf=0.0), n_simulations=8000).expected_position[9]
        strong = grid(dnf=0.0)
        strong[9].form, strong[9].elo = 3.0, ELO_BASELINE + 200
        assert simulate(strong, n_simulations=8000).expected_position[9] < base - 0.5

    def test_grid_still_dominates_form(self):
        """The backtest's central finding: the grid is the signal."""
        entries = grid(dnf=0.0)
        for i, e in enumerate(entries):       # form runs backwards against the grid
            e.form = float(22 - i)
        sim = simulate(entries, n_simulations=4000)
        assert sim.expected_position[0] < sim.expected_position[21]

    def test_missing_grid_is_provisional_and_ordered_by_form(self):
        entries = [Entry(f"d{i}", TEAMS[i // 2], None, ELO_BASELINE, float(22 - i), 0.1) for i in range(22)]
        sim = simulate(entries, n_simulations=3000)
        assert sim.grid_provisional
        assert sim.expected_position[21] < sim.expected_position[0]   # best form starts first

    def test_pit_lane_start_goes_to_the_back(self):
        entries = grid()
        entries[0].grid = 0
        sim = simulate(entries, n_simulations=200)
        assert not sim.grid_provisional and sim.grid[0] == 22

    def test_same_seed_same_result(self):
        a, b = simulate(grid(), n_simulations=500, seed=3), simulate(grid(), n_simulations=500, seed=3)
        assert np.array_equal(a.positions, b.positions)

    def test_summary_shape(self):
        s = simulate(grid(), n_simulations=300).summary()["d00"]
        assert len(s["position_distribution"]) == 22
        assert sum(s["position_distribution"]) == 300
        assert {"expected_points", "eleventh_probability", "retire_probability"} <= set(s)


class TestFormBook:
    def _race(self, order, out=()):
        return [
            ObservedResult(d, f"team_{d[0]}", None if d in out else i, d not in out)
            for i, d in enumerate(order, start=1)
        ]

    def test_no_history_falls_back_to_the_prior(self):
        book = FormBook()
        book.begin(2026)
        assert book.form("x", "t", 7.0) == pytest.approx(7.0)
        assert book.dnf_probability("x", "t") == pytest.approx(DEFAULT_PARAMS.league_dnf_prior)

    def test_form_tracks_results_and_recent_ones_weigh_more(self):
        book = FormBook()
        for _ in range(6):
            book.observe(2026, self._race(["a1", "b1", "c1"]))
        assert book.form("a1", "team_a", 10.0) < book.form("c1", "team_c", 10.0)
        before = book.form("a1", "team_a", 10.0)
        book.observe(2026, self._race(["c1", "b1", "a1"]))
        assert book.form("a1", "team_a", 10.0) > before

    def test_reliability_is_shrunk_towards_the_team_and_league(self):
        book = FormBook()
        for _ in range(10):
            book.observe(2026, self._race(["a1", "a2", "b1", "b2"], out={"a1"}))
        fragile, team_mate, other = (
            book.dnf_probability("a1", "team_a"),
            book.dnf_probability("a2", "team_a"),
            book.dnf_probability("b1", "team_b"),
        )
        assert fragile > team_mate > other          # driver > team > league effect
        assert fragile < 1.0                        # never taken at face value

    def test_sprints_inform_form_but_not_reliability(self):
        book = FormBook()
        book.observe(2026, self._race(["a1", "b1"], out={"b1"}), sprint=True)
        assert book.has_history("a1")
        assert book.dnf_probability("b1", "team_b") == pytest.approx(
            DEFAULT_PARAMS.league_dnf_prior
        )

    def test_eleventh_place_record(self):
        book = FormBook()
        order = [f"d{i}" for i in range(1, 13)]
        book.observe(2026, [ObservedResult(d, "t", i, True) for i, d in enumerate(order, 1)])
        book.observe(2026, [ObservedResult(d, "t", i, True) for i, d in enumerate(order, 1)], sprint=True)
        assert book.eleventh_counts() == {"d11": 2}

    def test_regulation_reset_wipes_the_book(self):
        book = FormBook()
        for _ in range(5):
            book.observe(2025, self._race(["a1", "b1"]))
        assert book.has_history("a1")
        book.begin(2026)
        assert not book.has_history("a1") and book.events_seen == 0

    def test_same_era_keeps_history(self):
        book = FormBook()
        book.observe(2024, self._race(["a1", "b1"]))
        book.begin(2025)
        assert book.has_history("a1")


class TestExactOptimiser:
    def test_takes_the_ideal_shape_on_the_best_drivers(self):
        entries = grid()
        result = optimize_allocation(simulate(entries, n_simulations=4000), team_of(entries))
        assert sorted(result.allocation.values(), reverse=True) == [5, 5, 3, 1, 1]
        assert result.errors == []
        assert list(result.allocation)[:2] == ["d00", "d02"]      # P1, then P3: P2 is a team-mate

    def test_never_picks_two_drivers_of_one_team(self):
        entries = grid()
        for risk in RISK_PROFILES.values():
            alloc = optimize_allocation(
                simulate(entries, n_simulations=2000), team_of(entries), risk_aversion=risk
            ).allocation
            teams = [team_of(entries)[d] for d in alloc]
            assert len(set(teams)) == len(teams)
            assert sum(alloc.values()) == R.total_chips

    def test_matches_brute_force(self):
        """The dynamic programme must find the true optimum."""
        rng = np.random.default_rng(1)
        ids = [f"d{i}" for i in range(12)]
        teams = {d: f"t{i // 2}" for i, d in enumerate(ids)}
        mu = rng.uniform(0, 20, 12).tolist()
        p = rng.uniform(0, 0.6, 12).tolist()
        picks = exact_ev_allocation(ids, mu, p, teams, R)
        got = sum(c * mu[i] - 10 * p[i] for i, c in picks.items())

        best = -1e9
        for combo in itertools.combinations(range(12), 5):
            if len({teams[ids[i]] for i in combo}) < 5:
                continue
            for chips in set(itertools.permutations([5, 5, 3, 1, 1])):
                best = max(best, sum(c * mu[i] - 10 * p[i] for i, c in zip(combo, chips)))
        assert got == pytest.approx(best)

    def test_flat_penalty_makes_an_unreliable_cheap_pick_unattractive(self):
        """A 1-chip driver likely to retire costs more than it can earn."""
        ids = ["a", "b", "c", "d", "safe", "risky"]
        teams = {d: d for d in ids}
        mu = [20, 15, 10, 5, 1.0, 2.0]
        p = [0, 0, 0, 0, 0.0, 0.5]          # risky: +2 expected, -5 expected penalty
        picks = exact_ev_allocation(ids, mu, p, teams, R)
        assert 4 in picks and 5 not in picks

    def test_infeasible_field_reports_instead_of_hanging(self):
        entries = grid(n=6)                 # only three teams
        result = optimize_allocation(simulate(entries, n_simulations=300), team_of(entries))
        assert result.allocation == {} and result.errors

    def test_drivers_without_a_team_are_never_picked(self):
        entries = grid()
        teams = team_of(entries)
        teams.pop("d00")
        result = optimize_allocation(simulate(entries, n_simulations=1000), teams)
        assert "d00" not in result.allocation and result.errors == []

    def test_concentrating_beats_spreading_even_in_the_bad_case(self):
        """Why the risk profiles barely matter: spreading chips loses on the
        mean *and* on the 10th percentile."""
        from app.optimizer.pool_optimizer import _portfolio

        sim = simulate(grid(), n_simulations=8000)
        def scores(alloc):
            chips = np.zeros(22, dtype=np.int64)
            for i, c in alloc.items():
                chips[i] = c
            return _portfolio(sim, chips, R)
        focus, spread = scores({0: 5, 2: 5, 4: 3, 6: 1, 8: 1}), scores({0: 3, 2: 3, 4: 3, 6: 3, 8: 3})
        assert focus.mean() > spread.mean() + 20
        assert np.percentile(focus, 10) > np.percentile(spread, 10)

    def test_eleventh_pick_is_a_midfield_driver(self):
        sim = simulate(grid(), n_simulations=6000)
        (best, p), *_ = pick_eleventh(sim)
        assert 8 <= sim.index[best] <= 15
        assert 0.05 < p < 0.25              # never a favourite; about one in ten


class TestEngine:
    def _drivers(self, with_grid=True):
        return [
            {"driver_id": f"d{i}", "team_id": TEAMS[i // 2], "code": f"D{i:02d}",
             "name": f"Driver {i}", "grid_position": i + 1 if with_grid else None,
             "elo": ELO_BASELINE}
            for i in range(22)
        ]

    def test_strategy_is_a_complete_valid_bet(self):
        drivers = self._drivers()
        tm = {d["driver_id"]: d["team_id"] for d in drivers}
        s = StrategyEngine().generate_strategy(drivers, {}, {}, tm, n_simulations=3000)
        assert sum(s["allocation"].values()) == 15
        assert R.validate(s["allocation"], tm) == [] and s["constraint_errors"] == []
        assert s["eleventh_pick"]["driver_id"] in tm
        assert len(s["eleventh_candidates"]) == 3
        assert 0 < s["total_ev"] < s["max_score"] == 332
        assert len(s["field"]) == 22
        assert [d["tokens"] for d in s["driver_details"]] == [5, 5, 3, 1, 1]

    def test_sprint_strategy_uses_the_sprint_scale(self):
        drivers = self._drivers()
        tm = {d["driver_id"]: d["team_id"] for d in drivers}
        s = StrategyEngine().generate_strategy(drivers, {}, {}, tm, is_sprint=True, n_simulations=2000)
        assert s["max_score"] == 304 and s["is_sprint"]

    def test_provisional_grid_is_flagged(self):
        drivers = self._drivers(with_grid=False)
        tm = {d["driver_id"]: d["team_id"] for d in drivers}
        s = StrategyEngine().generate_strategy(drivers, {}, {}, tm, n_simulations=1000)
        assert s["grid_provisional"] is True
        assert any("classificação" in i for i in s["insights"])


class TestBacktest:
    def _events(self, n=6):
        events = []
        for rnd in range(1, n + 1):
            rows = [
                EventRow(f"d{i}", TEAMS[i // 2], i + 1, None if i == 21 else i + 1,
                         classified=i != 21, retired=i == 21, code=f"D{i:02d}")
                for i in range(22)
            ]
            events.append(Event(2026, rnd, False, f"2026-03-{rnd:02d}", f"GP {rnd}", rows))
        return events

    def test_perfectly_predictable_season_scores_the_maximum_chips(self):
        """If every race finishes in grid order, betting the grid is perfect."""
        report = walk_forward(self._events(), strategy="grid", n_simulations=300)
        # 5x25 + 5x15 + 3x10 + 1x6 + 1x2: P1, P3, P5, P7, P9 are the first five teams.
        assert all(o.chip_score == 238 for o in report.outcomes)
        assert report.hits() == 6               # grid P11 finishes 11th every time

    def test_engine_matches_the_grid_when_the_grid_is_the_truth(self):
        events = self._events()
        engine = walk_forward(events, strategy="model", n_simulations=1500)
        assert engine.chip_total() == pytest.approx(238 * 6)
        assert 0 < engine.rmse() < 6 and engine.ll11() > -3.04

    def test_only_requested_seasons_are_bet_on(self):
        events = self._events(3)
        events[0].season = 2025
        report = walk_forward(events, {2026}, n_simulations=200)
        assert report.events() == 2 and report.events(2025) == 0
