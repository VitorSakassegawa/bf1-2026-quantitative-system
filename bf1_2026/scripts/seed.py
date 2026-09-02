"""Seed the database with the current teams and driver grid.

Idempotent: running it again updates existing rows instead of duplicating.
The historical ingestion step also auto-creates any older drivers/teams it
finds, so this seed only needs to establish the *current* active grid that
the predictor will produce strategies for.

    python -m scripts.seed
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import func, select

from app.database import AsyncSessionLocal
from app.models.circuit import Circuit, TrackType
from app.models.driver import Driver
from app.models.race import Race, RaceStatus
from app.models.team import Team

# Current grid: 11 teams, 2 drivers each (22 drivers).
#
# 2026 is a regulation-reset season: Cadillac joins as the 11th team and Audi
# takes over the Sauber entry as a full works constructor. Only one driver
# changed teams (Hadjar, promoted to Red Bull); Lindblad is the sole rookie.
#
# Driver `code` is the stable key the ingestion step matches results against
# (Ergast/Jolpica uses the same 3-letter codes).
GRID: list[dict] = [
    {
        "team": "McLaren",
        "full_name": "McLaren Formula 1 Team",
        "drivers": [
            {"name": "Lando Norris", "code": "NOR", "number": 4, "nat": "British"},
            {"name": "Oscar Piastri", "code": "PIA", "number": 81, "nat": "Australian"},
        ],
    },
    {
        "team": "Ferrari",
        "full_name": "Scuderia Ferrari",
        "drivers": [
            {"name": "Charles Leclerc", "code": "LEC", "number": 16, "nat": "Monegasque"},
            {"name": "Lewis Hamilton", "code": "HAM", "number": 44, "nat": "British"},
        ],
    },
    {
        "team": "Red Bull",
        "full_name": "Oracle Red Bull Racing",
        "drivers": [
            {"name": "Max Verstappen", "code": "VER", "number": 1, "nat": "Dutch"},
            {"name": "Isack Hadjar", "code": "HAD", "number": 6, "nat": "French"},
        ],
    },
    {
        "team": "Mercedes",
        "full_name": "Mercedes-AMG Petronas F1 Team",
        "drivers": [
            {"name": "George Russell", "code": "RUS", "number": 63, "nat": "British"},
            {"name": "Andrea Kimi Antonelli", "code": "ANT", "number": 12, "nat": "Italian"},
        ],
    },
    {
        "team": "Aston Martin",
        "full_name": "Aston Martin Aramco F1 Team",
        "drivers": [
            {"name": "Fernando Alonso", "code": "ALO", "number": 14, "nat": "Spanish"},
            {"name": "Lance Stroll", "code": "STR", "number": 18, "nat": "Canadian"},
        ],
    },
    {
        "team": "Alpine",
        "full_name": "BWT Alpine F1 Team",
        "drivers": [
            {"name": "Pierre Gasly", "code": "GAS", "number": 10, "nat": "French"},
            {"name": "Franco Colapinto", "code": "COL", "number": 43, "nat": "Argentine"},
        ],
    },
    {
        "team": "Williams",
        "full_name": "Williams Racing",
        "drivers": [
            {"name": "Alexander Albon", "code": "ALB", "number": 23, "nat": "Thai"},
            {"name": "Carlos Sainz", "code": "SAI", "number": 55, "nat": "Spanish"},
        ],
    },
    {
        "team": "Racing Bulls",
        "full_name": "Visa Cash App Racing Bulls F1 Team",
        "drivers": [
            {"name": "Liam Lawson", "code": "LAW", "number": 30, "nat": "New Zealander"},
            {"name": "Arvid Lindblad", "code": "LIN", "number": 41, "nat": "British"},
        ],
    },
    {
        "team": "Haas",
        "full_name": "MoneyGram Haas F1 Team",
        "drivers": [
            {"name": "Esteban Ocon", "code": "OCO", "number": 31, "nat": "French"},
            {"name": "Oliver Bearman", "code": "BEA", "number": 87, "nat": "British"},
        ],
    },
    {
        "team": "Audi",
        "full_name": "Audi F1 Team",
        "drivers": [
            {"name": "Nico Hulkenberg", "code": "HUL", "number": 27, "nat": "German"},
            {"name": "Gabriel Bortoleto", "code": "BOR", "number": 5, "nat": "Brazilian"},
        ],
    },
    {
        "team": "Cadillac",
        "full_name": "Cadillac Formula 1 Team",
        "drivers": [
            {"name": "Sergio Perez", "code": "PER", "number": 11, "nat": "Mexican"},
            {"name": "Valtteri Bottas", "code": "BOT", "number": 77, "nat": "Finnish"},
        ],
    },
]


async def upsert_team(session, name: str, full_name: str) -> Team:
    result = await session.execute(select(Team).where(Team.name == name))
    team = result.scalar_one_or_none()
    if team is None:
        team = Team(name=name, full_name=full_name, is_active=True)
        session.add(team)
        await session.flush()
        logger.info(f"Created team {name}")
    else:
        team.full_name = full_name
        team.is_active = True
    return team


async def upsert_driver(session, team: Team, d: dict) -> Driver:
    result = await session.execute(select(Driver).where(Driver.code == d["code"]))
    driver = result.scalar_one_or_none()
    if driver is None:
        driver = Driver(
            name=d["name"],
            code=d["code"],
            number=d["number"],
            nationality=d["nat"],
            team_id=team.id,
            is_active=True,
        )
        session.add(driver)
        logger.info(f"Created driver {d['code']} ({d['name']})")
    else:
        driver.name = d["name"]
        driver.number = d["number"]
        driver.nationality = d["nat"]
        driver.team_id = team.id
        driver.is_active = True
    return driver


# 2026 calendar: 22 rounds.
#
# The Bahrain and Saudi Arabian Grands Prix (originally rounds 4 and 5) were
# cancelled and not replaced, so the season runs to 22 races and every round
# from Miami onwards is numbered two lower than the original schedule.
#
# `time_utc` is the scheduled race start converted to UTC. It matters: the
# betting deadline is derived from the race start, so a placeholder time
# (this used to be a blanket 14:00 UTC) leaves bets open after the flag has
# dropped. Saturday races (Baku, Las Vegas) are marked accordingly — the Las
# Vegas night race starts on the Sunday in UTC.
#
# Sprint rounds in 2026: China, Miami, Canada, Great Britain, Netherlands
# and Singapore.
CALENDAR_2026: list[dict] = [
    {"round": 1, "name": "Australian GP", "date": "2026-03-08", "time_utc": "04:00", "sprint": False, "country": "Australia", "city": "Melbourne"},
    {"round": 2, "name": "Chinese GP", "date": "2026-03-15", "time_utc": "07:00", "sprint": True, "country": "China", "city": "Shanghai"},
    {"round": 3, "name": "Japanese GP", "date": "2026-03-29", "time_utc": "05:00", "sprint": False, "country": "Japan", "city": "Suzuka"},
    {"round": 4, "name": "Miami GP", "date": "2026-05-03", "time_utc": "20:00", "sprint": True, "country": "USA", "city": "Miami"},
    {"round": 5, "name": "Canadian GP", "date": "2026-05-24", "time_utc": "18:00", "sprint": True, "country": "Canada", "city": "Montreal"},
    {"round": 6, "name": "Monaco GP", "date": "2026-06-07", "time_utc": "13:00", "sprint": False, "country": "Monaco", "city": "Monte Carlo"},
    {"round": 7, "name": "Spanish GP", "date": "2026-06-14", "time_utc": "13:00", "sprint": False, "country": "Spain", "city": "Barcelona"},
    {"round": 8, "name": "Austrian GP", "date": "2026-06-28", "time_utc": "13:00", "sprint": False, "country": "Austria", "city": "Spielberg"},
    {"round": 9, "name": "British GP", "date": "2026-07-05", "time_utc": "14:00", "sprint": True, "country": "UK", "city": "Silverstone"},
    {"round": 10, "name": "Belgian GP", "date": "2026-07-19", "time_utc": "13:00", "sprint": False, "country": "Belgium", "city": "Spa"},
    {"round": 11, "name": "Hungarian GP", "date": "2026-07-26", "time_utc": "13:00", "sprint": False, "country": "Hungary", "city": "Budapest"},
    {"round": 12, "name": "Dutch GP", "date": "2026-08-23", "time_utc": "13:00", "sprint": True, "country": "Netherlands", "city": "Zandvoort"},
    {"round": 13, "name": "Italian GP", "date": "2026-09-06", "time_utc": "13:00", "sprint": False, "country": "Italy", "city": "Monza"},
    {"round": 14, "name": "Madrid GP", "date": "2026-09-13", "time_utc": "13:00", "sprint": False, "country": "Spain", "city": "Madrid"},
    {"round": 15, "name": "Azerbaijan GP", "date": "2026-09-26", "time_utc": "11:00", "sprint": False, "country": "Azerbaijan", "city": "Baku"},
    {"round": 16, "name": "Singapore GP", "date": "2026-10-11", "time_utc": "12:00", "sprint": True, "country": "Singapore", "city": "Singapore"},
    {"round": 17, "name": "United States GP", "date": "2026-10-25", "time_utc": "19:00", "sprint": False, "country": "USA", "city": "Austin"},
    {"round": 18, "name": "Mexico City GP", "date": "2026-11-01", "time_utc": "20:00", "sprint": False, "country": "Mexico", "city": "Mexico City"},
    {"round": 19, "name": "Brazilian GP", "date": "2026-11-08", "time_utc": "17:00", "sprint": False, "country": "Brazil", "city": "Sao Paulo"},
    {"round": 20, "name": "Las Vegas GP", "date": "2026-11-22", "time_utc": "04:00", "sprint": False, "country": "USA", "city": "Las Vegas"},
    {"round": 21, "name": "Qatar GP", "date": "2026-11-29", "time_utc": "16:00", "sprint": False, "country": "Qatar", "city": "Lusail"},
    {"round": 22, "name": "Abu Dhabi GP", "date": "2026-12-06", "time_utc": "13:00", "sprint": False, "country": "UAE", "city": "Yas Marina"},
]


async def _link_circuit(session, gp: dict) -> Circuit:
    """Reuse the circuit for that city, else create a new one.

    Matching on country alone collapsed distinct venues into one row: the three
    US rounds (Miami, Austin, Las Vegas) all shared a single circuit, as did
    Barcelona and Madrid. Circuit KPIs (overtaking index, DNF rate, tyre
    degradation) are venue-specific, so that silently blended Monaco-like and
    Monza-like tracks together. City + country is the unique key.
    """
    existing = (
        await session.execute(
            select(Circuit).where(
                func.lower(Circuit.city) == gp["city"].lower(),
                func.lower(Circuit.country) == gp["country"].lower(),
            )
        )
    ).scalars().first()
    if existing:
        return existing
    circuit = Circuit(
        name=f"{gp['city']} Circuit",
        country=gp["country"],
        city=gp["city"],
        track_type=TrackType.mixed,
    )
    session.add(circuit)
    await session.flush()
    return circuit


async def seed_calendar(session, season: int = 2026) -> int:
    """Create the season's races (idempotent by season+round)."""
    created = 0
    for gp in CALENDAR_2026:
        exists = (
            await session.execute(
                select(Race.id).where(
                    Race.season == season, Race.round_number == gp["round"]
                )
            )
        ).scalar_one_or_none()
        if exists:
            continue
        circuit = await _link_circuit(session, gp)
        hour, minute = (int(x) for x in gp["time_utc"].split(":"))
        race_date = datetime.strptime(gp["date"], "%Y-%m-%d").replace(
            hour=hour, minute=minute, tzinfo=timezone.utc
        )
        session.add(
            Race(
                circuit_id=circuit.id,
                season=season,
                round_number=gp["round"],
                race_name=gp["name"],
                race_date=race_date,
                is_sprint_weekend=gp["sprint"],
                status=RaceStatus.scheduled,
                deadline_bets=race_date - timedelta(hours=1),
            )
        )
        created += 1
    return created


async def seed() -> None:
    async with AsyncSessionLocal() as session:
        for entry in GRID:
            team = await upsert_team(session, entry["team"], entry["full_name"])
            for d in entry["drivers"]:
                await upsert_driver(session, team, d)
        created = await seed_calendar(session)
        await session.commit()
    logger.info(
        f"Seed complete: {len(GRID)} teams, {len(GRID) * 2} drivers, "
        f"{created} new 2026 races"
    )


if __name__ == "__main__":
    asyncio.run(seed())
