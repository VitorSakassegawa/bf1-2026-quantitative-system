"""Post-pipeline smoke test.

Validates that the system is actually ready after `scripts.pipeline`:
  * the database holds a believable season (grid, calendar, results — and in
    particular results for the season *in progress*, which earlier versions
    of the pipeline silently never stored)
  * the engine produces a bet that obeys every rule of the pool
  * the pool site can be read, when it is configured

Prints a PASS/FAIL report and exits non-zero on any failure, so it can be wired
into CI or a deploy step:

    docker exec bf1_api python -m scripts.healthcheck
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.database import AsyncSessionLocal
from app.models.circuit import Circuit
from app.models.driver import Driver
from app.models.race import Race
from app.models.race_result import RaceResult
from app.models.team import Team
from app.rules.pool_rules import DEFAULT_RULES
from app.services import pool_service
from app.services.strategy_service import build_strategy, get_next_or_latest_race
from app.utils.guardrails import DRY_EXTREME_MAX_STOPS, SPRINT_WEEKEND_MAX_STOPS

# Smaller sim count keeps the check fast.
HEALTHCHECK_SIMS = 4000

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


class Report:
    def __init__(self) -> None:
        self.checks: list[tuple[str, bool | None, str]] = []

    def add(self, name: str, ok: bool | None, detail: str = "") -> None:
        """ok=None records a skipped check (shown, never fails the run)."""
        self.checks.append((name, ok, detail))

    def ok(self) -> bool:
        return all(ok is not False for _, ok, _ in self.checks)

    def render(self) -> str:
        lines = ["", "=== BF1-2026 Health Check ===", ""]
        for name, ok, detail in self.checks:
            mark = (
                f"{GREEN}PASS{RESET}" if ok
                else f"{YELLOW}SKIP{RESET}" if ok is None
                else f"{RED}FAIL{RESET}"
            )
            lines.append(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
        lines.append("")
        verdict = f"{GREEN}ALL CHECKS PASSED{RESET}" if self.ok() else f"{RED}CHECKS FAILED{RESET}"
        lines.append(f"  {verdict}")
        lines.append("")
        return "\n".join(lines)


async def _data_checks(session, report: Report) -> Race | None:
    drivers = await session.scalar(
        select(func.count()).select_from(Driver).where(Driver.is_active == True)  # noqa: E712
    )
    active_teams = await session.scalar(
        select(func.count(func.distinct(Driver.team_id))).where(Driver.is_active == True)  # noqa: E712
    )
    circuits = await session.scalar(select(func.count()).select_from(Circuit))
    races = await session.scalar(select(func.count()).select_from(Race))
    results = await session.scalar(select(func.count()).select_from(RaceResult))

    report.add("Active drivers >= 10", (drivers or 0) >= 10, f"{drivers} found")
    report.add(
        f"Active drivers span >= {DEFAULT_RULES.minimum_drivers} teams",
        (active_teams or 0) >= DEFAULT_RULES.minimum_drivers,
        f"{active_teams} teams",
    )
    report.add("Circuits present", (circuits or 0) > 0, f"{circuits} found")
    report.add("Races present", (races or 0) > 0, f"{races} found")
    report.add(
        "Historical results ingested",
        (results or 0) > 0,
        f"{results} rows" + ("" if results else " — run scripts.ingest"),
    )

    # The season in progress must have results once it has started. This is
    # the check that would have caught the old pipeline never storing them.
    now = datetime.now(timezone.utc)
    season = await session.scalar(select(func.max(Race.season)).where(Race.season <= now.year))
    if season is not None:
        started = await session.scalar(
            select(func.count()).select_from(Race).where(
                Race.season == season, Race.race_date < now
            )
        )
        with_results = await session.scalar(
            select(func.count(func.distinct(RaceResult.race_id)))
            .join(Race, Race.id == RaceResult.race_id)
            .where(Race.season == season)
        )
        # Results appear on the API a few hours after the flag; tolerate the
        # most recent round being late.
        ok = (with_results or 0) >= max((started or 0) - 1, 0)
        report.add(
            f"{season} results are up to date",
            ok,
            f"{with_results} of {started} completed rounds stored"
            + ("" if ok else " — run scripts.ingest"),
        )
        unattributed = await session.scalar(
            select(func.count()).select_from(RaceResult).where(RaceResult.team_id.is_(None))
        )
        report.add(
            "Every result carries its team",
            (unattributed or 0) == 0,
            f"{unattributed} rows without one" if unattributed else "",
        )

    race = await get_next_or_latest_race(session)
    report.add("A target race is available", race is not None,
               race.race_name if race else "none")
    return race


def _bet_checks(strategy: dict, team_of: dict[str, str], report: Report) -> None:
    rules = DEFAULT_RULES
    allocation = strategy.get("allocation", {})
    total = sum(allocation.values())
    report.add(f"Chips sum to {rules.total_chips}", total == rules.total_chips, f"got {total}")
    report.add(
        f"Each driver 1-{rules.max_chips_per_driver} chips",
        all(1 <= t <= rules.max_chips_per_driver for t in allocation.values()),
        f"{sorted(allocation.values(), reverse=True)}",
    )
    report.add(
        f"At least {rules.minimum_drivers} drivers",
        len(allocation) >= rules.minimum_drivers,
        f"{len(allocation)} picked",
    )
    teams = [team_of.get(d) for d in allocation]
    report.add(
        "One driver per team",
        None not in teams and len(set(teams)) == len(teams),
        f"{len(set(teams))} teams for {len(teams)} drivers",
    )
    report.add(
        "Bet passes the pool's own validation",
        not strategy.get("constraint_errors"),
        "; ".join(strategy.get("constraint_errors", [])),
    )
    pick = strategy.get("eleventh_pick")
    report.add(
        "An 11th-place pick is made",
        bool(pick and pick.get("driver_id")),
        f"{pick['driver_code']} ({pick['probability']:.0%})" if pick else "none",
    )


def _guardrail_checks(strategy: dict, report: Report) -> None:
    field = strategy.get("field", [])
    probs_ok = all(
        0 <= d["top3_probability"] <= d["top10_probability"] + 1e-9 <= 1 + 1e-9
        and 0 <= d["dnf_probability"] <= 1
        for d in field
    )
    report.add("Probabilities valid & monotonic (top3<=top10)", probs_ok,
               f"{len(field)} drivers")

    # Across the field the win probabilities must add up to one race winner
    # (less the rare simulation in which nobody is classified).
    win_total = sum(d["win_probability"] for d in field)
    report.add("Win probabilities sum to ~1", 0.97 <= win_total <= 1.001, f"{win_total:.3f}")

    expected, ceiling = strategy.get("total_ev", 0.0), strategy.get("max_score", 0.0)
    report.add(
        "Expected score below the pool maximum",
        0 < expected < ceiling,
        f"{expected:.1f} of {ceiling:.0f}",
    )

    pit = strategy.get("pit_stop_projection", {})
    stops = pit.get("projected_stops")
    ceiling_stops = (
        SPRINT_WEEKEND_MAX_STOPS + 1
        if strategy.get("race", {}).get("is_sprint_weekend")
        else DRY_EXTREME_MAX_STOPS
    )
    report.add(
        "Pit-stop projection physically bounded",
        stops is not None and 0 <= stops <= ceiling_stops,
        f"{pit.get('label', 'n/a')}",
    )


async def _pool_checks(report: Report) -> None:
    if not pool_service.pool_enabled():
        report.add("Pool site readable", None, "POOL_* not configured")
        return
    try:
        standings = await pool_service.fetch_standings()
        event = await pool_service.fetch_next_event()
        me = standings.get("me")
        report.add(
            "Pool site readable",
            bool(standings.get("entries")),
            f"{len(standings.get('entries', []))} participants"
            + (f", you are {me['position']}º" if me else ", your row was not identified"),
        )
        same = event["rules"] == DEFAULT_RULES
        report.add(
            "Pool rules match the engine's defaults",
            True if same else None,
            "" if same else "the pool's live rules differ and will be used instead",
        )
    except Exception as e:  # noqa: BLE001
        report.add("Pool site readable", False, str(e))
    finally:
        await pool_service.get_pool_client().close()


async def main() -> int:
    report = Report()
    try:
        async with AsyncSessionLocal() as session:
            race = await _data_checks(session, report)

            if race is not None:
                strategy = await build_strategy(
                    session, race, aggressiveness="balanced",
                    n_simulations=HEALTHCHECK_SIMS,
                )
                report.add(
                    "Strategy generated without error", True,
                    f"{strategy['race']['name']}, grid: {strategy.get('grid_source')}",
                )
                team_of = {d["driver_id"]: d["team_id"] for d in strategy.get("field", [])}
                _bet_checks(strategy, team_of, report)
                _guardrail_checks(strategy, report)
            else:
                report.add("Strategy smoke test", False, "no race to analyze")
        await _pool_checks(report)
    except Exception as e:  # noqa: BLE001
        report.add("Healthcheck ran without exceptions", False, repr(e))

    print(report.render())
    return 0 if report.ok() else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
