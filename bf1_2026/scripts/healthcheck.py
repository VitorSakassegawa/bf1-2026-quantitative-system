"""Post-pipeline smoke test.

Validates that the system is actually ready after `scripts.pipeline`:
  * the database is populated (drivers, teams, circuits, results, a next race)
  * the engine produces a strategy that obeys every BF1-2026 constraint
  * the anti-hallucination guardrails are active (bounded pit stops, valid probs)

Prints a PASS/FAIL report and exits non-zero on any failure, so it can be wired
into CI or a deploy step:

    docker exec bf1_api python -m scripts.healthcheck
"""

from __future__ import annotations

import asyncio
import sys
import uuid

from sqlalchemy import func, select

from app.database import AsyncSessionLocal
from app.models.circuit import Circuit
from app.models.driver import Driver
from app.models.race import Race
from app.models.race_result import RaceResult
from app.models.team import Team
from app.services.strategy_service import build_strategy, get_next_or_latest_race
from app.utils.guardrails import (
    DRY_EXTREME_MAX_STOPS,
    SPRINT_WEEKEND_MAX_STOPS,
)
from app.utils.validators import (
    BF1_MAX_TOKENS_PER_DRIVER,
    BF1_MIN_DIFFERENT_TEAMS,
    BF1_MIN_DRIVERS,
    BF1_TOTAL_TOKENS,
)

# Smaller sim count keeps the check fast.
HEALTHCHECK_SIMS = 2000

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"


class Report:
    def __init__(self) -> None:
        self.checks: list[tuple[str, bool, str]] = []

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append((name, ok, detail))

    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.checks)

    def render(self) -> str:
        lines = ["", "=== BF1-2026 Health Check ===", ""]
        for name, ok, detail in self.checks:
            mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
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
    teams = await session.scalar(select(func.count()).select_from(Team))
    circuits = await session.scalar(select(func.count()).select_from(Circuit))
    results = await session.scalar(select(func.count()).select_from(RaceResult))
    races = await session.scalar(select(func.count()).select_from(Race))

    report.add("Active drivers >= 10", (drivers or 0) >= 10, f"{drivers} found")
    report.add("Teams >= 5", (teams or 0) >= 5, f"{teams} found")
    report.add("Circuits present", (circuits or 0) > 0, f"{circuits} found")
    report.add("Races present", (races or 0) > 0, f"{races} found")
    report.add(
        "Historical results ingested",
        (results or 0) > 0,
        f"{results} rows" + ("" if results else " — run scripts.ingest"),
    )

    race = await get_next_or_latest_race(session)
    report.add("A target race is available", race is not None,
               race.race_name if race else "none")
    return race


def _constraint_checks(strategy: dict, team_membership: dict, report: Report) -> None:
    allocation = strategy.get("allocation", {})
    total = sum(allocation.values())
    report.add(
        f"Tokens sum to {BF1_TOTAL_TOKENS}", total == BF1_TOTAL_TOKENS, f"got {total}"
    )
    report.add(
        f"Each driver 1-{BF1_MAX_TOKENS_PER_DRIVER} tokens",
        all(1 <= t <= BF1_MAX_TOKENS_PER_DRIVER for t in allocation.values()),
        f"{sorted(allocation.values(), reverse=True)}",
    )
    report.add(
        f"At least {BF1_MIN_DRIVERS} drivers",
        len(allocation) >= BF1_MIN_DRIVERS,
        f"{len(allocation)} picked",
    )
    teams_used = {team_membership.get(d) for d in allocation}
    report.add(
        f"At least {BF1_MIN_DIFFERENT_TEAMS} different teams",
        len(teams_used) >= BF1_MIN_DIFFERENT_TEAMS,
        f"{len(teams_used)} teams",
    )


def _guardrail_checks(strategy: dict, report: Report) -> None:
    # Probabilities within [0,1] and monotonic for every driver.
    probs_ok = True
    for d in strategy.get("driver_details", []):
        t3, t10 = d.get("top3_probability", 0), d.get("top10_probability", 0)
        dnf = d.get("dnf_probability", 0)
        if not (0 <= t3 <= 1 and 0 <= t10 <= 1 and 0 <= dnf <= 1 and t3 <= t10 + 1e-9):
            probs_ok = False
            break
    report.add("Probabilities valid & monotonic (top3<=top10)", probs_ok)

    pit = strategy.get("pit_stop_projection", {})
    stops = pit.get("projected_stops")
    is_sprint = strategy.get("is_sprint", False)
    # Sprint weekend GP: cap 2, +1 only under the rare extreme allowance.
    # Dry GP: cap 3, +1 only under the rare extreme allowance.
    ceiling = (SPRINT_WEEKEND_MAX_STOPS + 1) if is_sprint else DRY_EXTREME_MAX_STOPS
    report.add(
        "Pit-stop projection physically bounded",
        stops is not None and 0 <= stops <= ceiling,
        f"{pit.get('label', 'n/a')} (raw {pit.get('raw_estimate')}, "
        f"capped={pit.get('capped')})",
    )

    # Expected points must never exceed the BF1 ceiling.
    pts_ok = all(
        -10.01 <= d.get("expected_value", 0) <= 15 * 52  # generous portfolio bound
        for d in strategy.get("driver_details", [])
    )
    report.add("Expected values within sane bounds", pts_ok)


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
                report.add("Strategy generated without error", True,
                           f"EV={strategy.get('total_ev', 0):.1f}")

                # Build team_membership for the allocated drivers.
                alloc_ids = [uuid.UUID(d) for d in strategy.get("allocation", {})]
                membership: dict[str, str] = {}
                if alloc_ids:
                    rows = (
                        await session.execute(
                            select(Driver.id, Driver.team_id).where(
                                Driver.id.in_(alloc_ids)
                            )
                        )
                    ).all()
                    membership = {str(did): str(tid) for did, tid in rows}

                _constraint_checks(strategy, membership, report)
                _guardrail_checks(strategy, report)
            else:
                report.add("Strategy smoke test", False, "no race to analyze")
    except Exception as e:  # noqa: BLE001
        report.add("Healthcheck ran without exceptions", False, repr(e))

    print(report.render())
    return 0 if report.ok() else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
