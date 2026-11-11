"""Grid-anchored race simulator, calibrated on real results.

Why this replaced the previous Monte Carlo
------------------------------------------
A walk-forward backtest over 114 real events (2023-2026, races and sprints),
scored with the pool's actual rules, showed three things:

1. **The starting grid is the signal.** Simply betting the grid order would
   have scored 5,494 in the 2026 pool — third place — while the engine's
   default profile would have scored about 4,356 on the chip allocation
   alone. No model variant beat the grid by more than one standard error, so
   the model is *anchored* on the grid and only nudged by other evidence.
2. **The old noise was far too tight.** Finish-minus-grid has a standard
   deviation of ~2.8 positions at the front and ~4.5 at the back; the old
   simulator used 1.5.
3. **Retirements were underestimated fourfold.** 2026 runs at a 20 % DNF rate
   (Aston Martin 50 %, Cadillac 40 %, Racing Bulls 7 %); the engine assumed a
   flat 5 % for everyone.

What is in the latent score, and why
------------------------------------
    score = (1 - w) * grid + w * form - (elo - 1500) / elo_scale + noise

* ``form`` — exponentially weighted mean finishing position in the current
  regulation era, shrunk towards the team's form.
* ``elo`` — the long-memory rating.
* ``noise`` — Gaussian, twice as wide at the back of the grid as at the front.

Against a grid-only model, form and rating together improve the winner's
log-likelihood from -1.40 to -1.15 on 2023-25 (-1.44 to -1.35 on 2026) and
the podium Brier score from 0.064 to 0.061. They do not change the realised
chip score by more than noise — which is the honest size of the edge.

Drivers are ranked by score among the cars still running; retirements are
drawn per driver from a recency-weighted, shrunk team/driver rate.

Outputs are kept as full simulation matrices so the optimiser can price any
allocation exactly — including the flat retirement penalty and the 11th-place
pick, neither of which the old expected-value shortcut could represent.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

from app.rules.pool_rules import DEFAULT_RULES, PoolRules
from app.utils.season_window import era_start_for

ELO_BASELINE = 1500.0


@dataclass(frozen=True)
class ModelParams:
    """Calibrated on 2023-25, checked out-of-sample on 2026. See module docs."""

    # Chosen for the best podium Brier score and near-best winner
    # log-likelihood, points RMSE and chip score on 2023-25, then confirmed on
    # 2026. Heavier form weights score better on all-field RMSE but worse on
    # the front of the field — the part the 5-5-3 chips are actually bet on.
    form_weight: float = 0.2
    noise_sigma: float = 1.7             # at pole; grows linearly to the back
    back_of_grid_sigma_ratio: float = 2.0
    elo_points_per_position: float = 300.0

    # Incidents: with this probability a classified car loses a chunk of
    # positions (damage, a penalty, a botched stop). Gaussian noise alone
    # cannot produce both a tight front of the field and the occasional
    # front-runner finishing 12th that real results show. Adding it improved
    # the winner log-likelihood (-1.35 -> -1.30) and the 11th-place
    # log-likelihood (-2.81 -> -2.73) on the held-out 2026 season.
    incident_rate: float = 0.12
    incident_scale: float = 6.0

    form_half_life_events: float = 6.0
    form_prior_strength: float = 1.5
    team_form_prior_strength: float = 2.0

    reliability_half_life_races: float = 12.0
    driver_reliability_prior: float = 12.0
    team_reliability_prior: float = 8.0
    league_dnf_prior: float = 0.14
    league_dnf_prior_strength: float = 20.0
    sprint_dnf_factor: float = 0.55   # sprints retire ~half as many cars
    min_dnf_probability: float = 0.01
    max_dnf_probability: float = 0.60
    # Share of non-finishes the pool actually penalises. A car that never
    # starts (DNS) scores nothing but is not charged the retirement penalty;
    # in 2026 that was 8 of 88 non-finishes.
    retirement_share: float = 0.92

    # With no qualifying result yet the grid is inferred from form, which is
    # a much weaker anchor. Not backtested against a real pre-qualifying
    # workflow — treat a provisional-grid strategy as a rough first look.
    provisional_grid_sigma_factor: float = 1.3
    # Rain adds variance and attrition. Directionally certain, magnitude an
    # assumption (too few wet races to calibrate).
    wet_sigma_gain: float = 0.5
    wet_dnf_gain: float = 0.5


DEFAULT_PARAMS = ModelParams()


@dataclass(frozen=True)
class ObservedResult:
    """One classified/unclassified line of a finished session."""

    driver_id: str
    team_id: str
    position: int | None
    classified: bool


@dataclass
class Entry:
    """A driver on the grid of the session being predicted."""

    driver_id: str
    team_id: str
    grid: int | None = None
    elo: float = ELO_BASELINE
    form: float | None = None
    dnf_probability: float | None = None


class FormBook:
    """Walk-forward form and reliability, per regulation era.

    Feed it finished sessions in chronological order with :meth:`observe`;
    ask it about a driver with :meth:`rate`. It forgets everything when the
    regulation era changes, because a reset season's pecking order has little
    to do with the previous one.
    """

    def __init__(self, params: ModelParams = DEFAULT_PARAMS) -> None:
        self.params = params
        self.era: int | None = None
        self._reset()

    def _reset(self) -> None:
        self._form: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        self._team_form: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        self._dnf: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        self._team_dnf: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        self._league = [0.0, 0.0]
        self._eleventh: dict[str, int] = defaultdict(int)
        self.events_seen = 0

    def begin(self, season: int) -> None:
        era = era_start_for(season)
        if self.era != era:
            self._reset()
            self.era = era

    def observe(
        self, season: int, results: Iterable[ObservedResult], sprint: bool = False
    ) -> None:
        self.begin(season)
        p = self.params
        decay = 0.5 ** (1.0 / p.form_half_life_events)
        for table in (self._form, self._team_form):
            for v in table.values():
                v[0] *= decay
                v[1] *= decay
        if not sprint:
            rdecay = 0.5 ** (1.0 / p.reliability_half_life_races)
            for table in (self._dnf, self._team_dnf):
                for v in table.values():
                    v[0] *= rdecay
                    v[1] *= rdecay
            self._league[0] *= rdecay
            self._league[1] *= rdecay

        for r in results:
            if r.classified and r.position == 11:
                self._eleventh[r.driver_id] += 1
            if r.classified and r.position:
                for table, key in ((self._form, r.driver_id), (self._team_form, r.team_id)):
                    table[key][0] += r.position
                    table[key][1] += 1.0
            if not sprint:
                failed = 0.0 if r.classified else 1.0
                for table, key in ((self._dnf, r.driver_id), (self._team_dnf, r.team_id)):
                    table[key][0] += failed
                    table[key][1] += 1.0
                self._league[0] += failed
                self._league[1] += 1.0
        self.events_seen += 1

    # -- queries --------------------------------------------------------- #
    def league_dnf_rate(self) -> float:
        p = self.params
        return (self._league[0] + p.league_dnf_prior * p.league_dnf_prior_strength) / (
            self._league[1] + p.league_dnf_prior_strength
        )

    def form(self, driver_id: str, team_id: str, fallback_position: float) -> float:
        """Shrunk mean finishing position; ``fallback_position`` is the prior."""
        p = self.params
        tf = self._team_form.get(team_id, (0.0, 0.0))
        team_form = (tf[0] + fallback_position * p.team_form_prior_strength) / (
            tf[1] + p.team_form_prior_strength
        )
        f = self._form.get(driver_id, (0.0, 0.0))
        return (f[0] + team_form * p.form_prior_strength) / (f[1] + p.form_prior_strength)

    def dnf_probability(self, driver_id: str, team_id: str) -> float:
        p = self.params
        td = self._team_dnf.get(team_id, (0.0, 0.0))
        team_rate = (td[0] + self.league_dnf_rate() * p.team_reliability_prior) / (
            td[1] + p.team_reliability_prior
        )
        d = self._dnf.get(driver_id, (0.0, 0.0))
        return (d[0] + team_rate * p.driver_reliability_prior) / (
            d[1] + p.driver_reliability_prior
        )

    def eleventh_counts(self) -> dict[str, int]:
        """How often each driver has finished exactly 11th in this era.

        Shown to the user next to the model's pick, not fed into it: blending
        this record into the probabilities was tested and did not improve the
        11th-place log-likelihood out-of-sample (it helped in 2026, where one
        team finished 11th in 10 of 26 events, and hurt in 2023-25).
        """
        return dict(self._eleventh)

    def has_history(self, driver_id: str) -> bool:
        return self._form.get(driver_id, (0.0, 0.0))[1] > 0.0

    def rate(self, entries: list[Entry]) -> list[Entry]:
        """Fill ``form`` and ``dnf_probability`` on entries that lack them."""
        n = len(entries)
        for e in entries:
            fallback = float(e.grid) if e.grid and e.grid > 0 else float(n)
            if e.form is None:
                e.form = self.form(e.driver_id, e.team_id, fallback)
            if e.dnf_probability is None:
                e.dnf_probability = self.dnf_probability(e.driver_id, e.team_id)
        return entries


@dataclass
class SimulationResult:
    driver_ids: list[str]
    sprint: bool
    grid_provisional: bool
    n_simulations: int
    grid: np.ndarray                  # (n,) grid actually used
    points: np.ndarray                # (sims, n) position points, 0 when not classified
    retired: np.ndarray               # (sims, n) bool: charged the retirement penalty
    out: np.ndarray                   # (sims, n) bool: not classified
    positions: np.ndarray             # (sims, n) int, non-finishers ranked last
    index: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.index = {d: i for i, d in enumerate(self.driver_ids)}
        fin = ~self.out
        self.expected_points = self.points.mean(axis=0)
        self.dnf_probability = self.out.mean(axis=0)
        self.retire_probability = self.retired.mean(axis=0)
        self.p11 = ((self.positions == 11) & fin).mean(axis=0)
        self.top3 = ((self.positions <= 3) & fin).mean(axis=0)
        self.top10 = ((self.positions <= 10) & fin).mean(axis=0)
        self.win = ((self.positions == 1) & fin).mean(axis=0)
        self.expected_position = self.positions.mean(axis=0)

    def summary(self) -> dict[str, dict]:
        """Per-driver summary in the shape the rest of the app consumes."""
        n = len(self.driver_ids)
        out: dict[str, dict] = {}
        for i, did in enumerate(self.driver_ids):
            dist = np.bincount(self.positions[:, i], minlength=n + 1)[1 : n + 1]
            out[did] = {
                "expected_position": round(float(self.expected_position[i]), 2),
                "avg_position": round(float(self.expected_position[i]), 2),
                "win_probability": round(float(self.win[i]), 4),
                "top3_probability": round(float(self.top3[i]), 4),
                "top10_probability": round(float(self.top10[i]), 4),
                "eleventh_probability": round(float(self.p11[i]), 4),
                "dnf_probability": round(float(self.dnf_probability[i]), 4),
                "dnf_probability_simulated": round(float(self.dnf_probability[i]), 4),
                "retire_probability": round(float(self.retire_probability[i]), 4),
                # Position points only: no penalty, no sprint doubling. Both
                # are applied by the optimiser, per the pool's formula.
                "expected_points": round(float(self.expected_points[i]), 3),
                "expected_bf1_points": round(float(self.expected_points[i]), 3),
                "position_distribution": dist.tolist(),
            }
        return out


def simulate(
    entries: list[Entry],
    *,
    sprint: bool = False,
    rules: PoolRules = DEFAULT_RULES,
    params: ModelParams = DEFAULT_PARAMS,
    n_simulations: int = 20000,
    seed: int = 42,
    weather_risk: float = 0.0,
) -> SimulationResult:
    """Simulate one session. Fully vectorised: 20k sims of 22 cars in ~50 ms."""
    n = len(entries)
    if n == 0:
        raise ValueError("simulate() needs at least one entry")
    n_simulations = max(int(n_simulations), 1)
    weather_risk = float(min(max(weather_risk, 0.0), 1.0))

    form = np.array(
        [e.form if e.form is not None else (e.grid or n) for e in entries], float
    )
    elo = np.array([e.elo if e.elo is not None else ELO_BASELINE for e in entries], float)

    has_grid = [bool(e.grid and e.grid > 0) for e in entries]
    provisional = not any(has_grid)
    if provisional:
        # No qualifying yet: order by form, then rating.
        order = sorted(range(n), key=lambda i: (form[i], -elo[i]))
        grid = np.empty(n)
        for rank, i in enumerate(order, start=1):
            grid[i] = rank
    else:
        # A car without a grid slot starts from the pit lane / back.
        grid = np.array(
            [float(e.grid) if ok else float(n) for e, ok in zip(entries, has_grid)]
        )

    p_out = np.array(
        [
            e.dnf_probability if e.dnf_probability is not None else params.league_dnf_prior
            for e in entries
        ],
        float,
    )
    if sprint:
        p_out = p_out * params.sprint_dnf_factor
    p_out = p_out * (1.0 + params.wet_dnf_gain * weather_risk)
    p_out = np.clip(p_out, params.min_dnf_probability, params.max_dnf_probability)

    mean = (1.0 - params.form_weight) * grid + params.form_weight * form
    mean = mean - (elo - ELO_BASELINE) / params.elo_points_per_position

    sigma = params.noise_sigma * (
        1.0 + (params.back_of_grid_sigma_ratio - 1.0) * (grid - 1.0) / max(n - 1, 1)
    )
    sigma = sigma * (1.0 + params.wet_sigma_gain * weather_risk)
    if provisional:
        sigma = sigma * params.provisional_grid_sigma_factor

    rng = np.random.default_rng(seed)
    score = mean[None, :] + rng.standard_normal((n_simulations, n)) * sigma[None, :]
    if params.incident_rate > 0:
        hit = rng.random((n_simulations, n)) < params.incident_rate
        loss = np.abs(rng.standard_normal((n_simulations, n))) * params.incident_scale
        score = score + np.where(hit, loss, 0.0)
    out = rng.random((n_simulations, n)) < p_out[None, :]
    retired = out & (rng.random((n_simulations, n)) < params.retirement_share)
    # Non-finishers sort behind every car still running.
    score = np.where(out, score + 1e6, score)
    positions = (score.argsort(axis=1).argsort(axis=1) + 1).astype(np.int16)

    table = np.zeros(n + 1, dtype=np.float32)
    for pos, pts in enumerate(rules.points_table(sprint), start=1):
        if pos <= n:
            table[pos] = pts
    points = np.where(out, 0.0, table[positions]).astype(np.float32)

    return SimulationResult(
        driver_ids=[e.driver_id for e in entries],
        sprint=sprint,
        grid_provisional=provisional,
        n_simulations=n_simulations,
        grid=grid,
        points=points,
        retired=retired,
        out=out,
        positions=positions,
    )
