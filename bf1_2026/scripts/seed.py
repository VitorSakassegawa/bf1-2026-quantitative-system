"""Seed the database with the current teams and driver grid.

Idempotent: running it again updates existing rows instead of duplicating.
The historical ingestion step also auto-creates any older drivers/teams it
finds, so this seed only needs to establish the *current* active grid that
the predictor will produce strategies for.

    python -m scripts.seed
"""

from __future__ import annotations

import asyncio

from loguru import logger
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.driver import Driver
from app.models.team import Team

# Current grid: 10 teams, 2 drivers each. Driver `code` is the stable key the
# ingestion step matches results against (Ergast uses the same 3-letter codes).
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
            {"name": "Yuki Tsunoda", "code": "TSU", "number": 22, "nat": "Japanese"},
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
            {"name": "Isack Hadjar", "code": "HAD", "number": 6, "nat": "French"},
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
        "team": "Kick Sauber",
        "full_name": "Stake F1 Team Kick Sauber",
        "drivers": [
            {"name": "Nico Hulkenberg", "code": "HUL", "number": 27, "nat": "German"},
            {"name": "Gabriel Bortoleto", "code": "BOR", "number": 5, "nat": "Brazilian"},
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


async def seed() -> None:
    async with AsyncSessionLocal() as session:
        for entry in GRID:
            team = await upsert_team(session, entry["team"], entry["full_name"])
            for d in entry["drivers"]:
                await upsert_driver(session, team, d)
        await session.commit()
    logger.info(f"Seed complete: {len(GRID)} teams, {len(GRID) * 2} drivers")


if __name__ == "__main__":
    asyncio.run(seed())
