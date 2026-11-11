"""The BF1 pool's scoring rules — the single source of truth for the engine.

These mirror the pool's own machine-readable rules (the ``current_rules``
object its API returns) and the published "Regulamento BF1-2026":

    score = chips x driver points + 11th-place bonus - retirement penalties

Three things the engine used to get wrong, all of which change the optimal bet:

* The retirement penalty is **flat per driver** (-10), not per chip. Charging
  it per chip made a 5-chip pick look five times riskier than it is and pushed
  the optimiser towards spreading chips thin.
* **Two drivers from the same team are not allowed** at all. The old validator
  only required five *distinct* teams and let a sixth driver repeat one.
* Every bet also names the driver who finishes **11th**, worth 50 points. The
  engine had no notion of it.

Sprints are separate events scored on the sprint points table with the whole
score doubled (the pool's maximum for a sprint is 304 = 2 x (102 + 50), which
only works if the 11th-place bonus is doubled too; the retirement penalty is
assumed to double with it).

The scorer was validated against the pool's official scores for 26 real bets
of the 2026 season: 25 match exactly, and the one that does not is off by a
single post-race position change.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping

RACE_POINTS: tuple[int, ...] = (25, 18, 15, 12, 10, 8, 6, 4, 2, 1)
SPRINT_POINTS: tuple[int, ...] = (8, 7, 6, 5, 4, 3, 2, 1)


@dataclass(frozen=True)
class ResultLine:
    """One driver's outcome in a session, as the pool sees it."""

    position: int | None   # classified finishing position, None if not classified
    retired: bool = False  # started and did not finish -> retirement penalty
    # A driver who did not start (DNS) is neither classified nor penalised.


@dataclass(frozen=True)
class ScoreBreakdown:
    total: float
    chip_points: float
    eleventh_bonus: float
    retirement_penalty: float
    eleventh_hit: bool
    multiplier: float
    per_driver: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class PoolRules:
    total_chips: int = 15
    max_chips_per_driver: int = 5
    minimum_drivers: int = 5
    allow_same_team: bool = False
    eleventh_bonus: float = 50.0
    retirement_penalty: float = 10.0
    doubled_sprint_points: bool = True
    position_points: tuple[int, ...] = RACE_POINTS
    sprint_position_points: tuple[int, ...] = SPRINT_POINTS
    automatic_bet_penalty_percent: float = 20.0
    discard_enabled: bool = True
    # End-of-season bonuses for the championship bet.
    champion_bonus: float = 125.0
    vice_bonus: float = 100.0
    team_bonus: float = 85.0

    # ------------------------------------------------------------------ #
    @classmethod
    def from_pool_payload(cls, payload: Mapping | None) -> "PoolRules":
        """Build from the pool API's ``current_rules`` object.

        Unknown or missing keys keep their defaults, so a partial payload (or
        a renamed field after a site change) degrades to the published rules
        instead of failing.
        """
        if not payload:
            return cls()
        rules = cls()
        updates: dict = {}

        def take(src: str, dst: str, cast):
            if payload.get(src) is not None:
                try:
                    updates[dst] = cast(payload[src])
                except (TypeError, ValueError):
                    pass

        take("total_chips", "total_chips", int)
        take("max_chips_per_driver", "max_chips_per_driver", int)
        take("minimum_drivers", "minimum_drivers", int)
        take("eleventh_bonus", "eleventh_bonus", float)
        take("automatic_bet_penalty_percent", "automatic_bet_penalty_percent", float)
        take("doubled_sprint_points", "doubled_sprint_points", bool)
        take("discard_enabled", "discard_enabled", bool)
        # The two payloads that carry rules spell this differently.
        for key in ("allow_same_team", "same_team_allowed"):
            take(key, "allow_same_team", bool)
        if payload.get("retirement_penalty_enabled") is False:
            updates["retirement_penalty"] = 0.0
        else:
            take("retirement_penalty_points", "retirement_penalty", float)

        pts = payload.get("position_points")
        if isinstance(pts, (list, tuple)) and pts:
            table = tuple(int(p) for p in pts)
            while table and table[-1] == 0:      # drop the trailing zeros
                table = table[:-1]
            if table:
                key = (
                    "sprint_position_points"
                    if str(payload.get("race_type", "")).lower() == "sprint"
                    else "position_points"
                )
                updates[key] = table
        return replace(rules, **updates)

    # ------------------------------------------------------------------ #
    def points_table(self, sprint: bool = False) -> tuple[int, ...]:
        return self.sprint_position_points if sprint else self.position_points

    def points_for(self, position: int | None, sprint: bool = False) -> int:
        table = self.points_table(sprint)
        if position is None or position < 1 or position > len(table):
            return 0
        return table[position - 1]

    def multiplier(self, sprint: bool = False) -> float:
        return 2.0 if (sprint and self.doubled_sprint_points) else 1.0

    def max_score(self, sprint: bool = False) -> float:
        """Best conceivable score, the way the pool reports ``maximum_points``."""
        table = sorted(self.points_table(sprint), reverse=True)
        chips = self.ideal_chip_pattern()
        base = sum(c * p for c, p in zip(chips, table))
        return (base + self.eleventh_bonus) * self.multiplier(sprint)

    def ideal_chip_pattern(self) -> list[int]:
        """Chip counts that maximise a linear objective: 5-5-3-1-1 by default."""
        n = self.minimum_drivers
        chips = [1] * n
        left = self.total_chips - n
        for i in range(n):
            add = min(self.max_chips_per_driver - 1, left)
            chips[i] += add
            left -= add
        return chips

    # ------------------------------------------------------------------ #
    def validate(
        self,
        allocation: Mapping[str, int],
        team_membership: Mapping[str, str],
    ) -> list[str]:
        """Return every rule the allocation breaks (empty list when valid)."""
        errors: list[str] = []
        total = sum(allocation.values())
        if total != self.total_chips:
            errors.append(f"Total tokens must be {self.total_chips}, got {total}")

        for driver_id, chips in allocation.items():
            if chips < 1:
                errors.append(f"Driver {driver_id}: minimum 1 token, got {chips}")
            if chips > self.max_chips_per_driver:
                errors.append(
                    f"Driver {driver_id}: maximum {self.max_chips_per_driver} "
                    f"tokens, got {chips}"
                )

        if len(allocation) < self.minimum_drivers:
            errors.append(
                f"Must select at least {self.minimum_drivers} drivers, "
                f"got {len(allocation)}"
            )

        teams = [team_membership.get(d) for d in allocation]
        known = [t for t in teams if t]
        if len(set(known)) < self.minimum_drivers:
            errors.append(
                f"Must use at least {self.minimum_drivers} different teams, "
                f"got {len(set(known))}"
            )
        if not self.allow_same_team:
            if len(known) != len(teams):
                errors.append("Every selected driver must have a known team")
            dupes = sorted({t for t in known if known.count(t) > 1})
            if dupes:
                errors.append(
                    "Only one driver per team is allowed; repeated: "
                    + ", ".join(dupes)
                )
        return errors

    # ------------------------------------------------------------------ #
    def score(
        self,
        allocation: Mapping[str, int],
        eleventh_pick: str | None,
        results: Mapping[str, ResultLine],
        sprint: bool = False,
        automatic_penalty: bool = False,
    ) -> ScoreBreakdown:
        """Score a bet against a session's results, exactly as the pool does."""
        mult = self.multiplier(sprint)
        chip_points = 0.0
        penalty = 0.0
        per_driver: dict[str, float] = {}

        for driver_id, chips in allocation.items():
            line = results.get(driver_id)
            if line is None:
                per_driver[driver_id] = 0.0
                continue
            if line.retired:
                value = -self.retirement_penalty
                penalty += self.retirement_penalty
            else:
                value = chips * self.points_for(line.position, sprint)
                chip_points += value
            per_driver[driver_id] = value * mult

        actual_11th = next(
            (d for d, line in results.items() if line.position == 11 and not line.retired),
            None,
        )
        hit = eleventh_pick is not None and eleventh_pick == actual_11th
        bonus = self.eleventh_bonus if hit else 0.0

        total = (chip_points + bonus - penalty) * mult
        if automatic_penalty:
            total *= 1.0 - self.automatic_bet_penalty_percent / 100.0
        return ScoreBreakdown(
            total=round(total, 2),
            chip_points=chip_points * mult,
            eleventh_bonus=bonus * mult,
            retirement_penalty=penalty * mult,
            eleventh_hit=hit,
            multiplier=mult,
            per_driver=per_driver,
        )


DEFAULT_RULES = PoolRules()
