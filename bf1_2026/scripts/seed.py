"""Seed the database with the current teams, drivers and calendar.

Idempotent: running it again updates existing rows instead of duplicating.

This is only the *starting point* for a fresh database. The results sync
(`scripts.ingest`) is the authority from then on: it refreshes the calendar
from the results API and rewrites the active roster from the latest entry
list, so a mid-season driver change or a rescheduled race does not need a
code change here.

    python -m scripts.seed
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.circuit import Circuit, TrackType
from app.models.driver import Driver
from app.models.race import Race, RaceStatus
from app.models.team import Team

# The grid as of the 2026 Brazilian GP: 11 teams, 22 cars.
#
# 2026 is a regulation-reset season: Cadillac joined as the 11th team and Audi
# took over the Sauber entry. `ref` is the results API's stable identifier
# (driverId / constructorId) — the key the sync matches on. Names and
# three-letter codes are for display.
#
# Changes during the season that a static list cannot follow, and the sync
# does: Tsunoda stood in at Racing Bulls for rounds 12-14, Lawson raced for
# both Red Bull teams, and Haas ran Ocon, then Hirakawa, then Camara.
GRID: list[dict] = [
    {
        "team": "McLaren", "full_name": "McLaren Formula 1 Team", "ref": "mclaren",
        "drivers": [
            {"name": "Lando Norris", "code": "NOR", "number": 1, "nat": "British", "ref": "norris"},
            {"name": "Oscar Piastri", "code": "PIA", "number": 81, "nat": "Australian", "ref": "piastri"},
        ],
    },
    {
        "team": "Ferrari", "full_name": "Scuderia Ferrari", "ref": "ferrari",
        "drivers": [
            {"name": "Charles Leclerc", "code": "LEC", "number": 16, "nat": "Monegasque", "ref": "leclerc"},
            {"name": "Lewis Hamilton", "code": "HAM", "number": 44, "nat": "British", "ref": "hamilton"},
        ],
    },
    {
        "team": "Red Bull", "full_name": "Oracle Red Bull Racing", "ref": "red_bull",
        "drivers": [
            {"name": "Max Verstappen", "code": "VER", "number": 3, "nat": "Dutch", "ref": "max_verstappen"},
            {"name": "Isack Hadjar", "code": "HAD", "number": 6, "nat": "French", "ref": "hadjar"},
        ],
    },
    {
        "team": "Mercedes", "full_name": "Mercedes-AMG Petronas F1 Team", "ref": "mercedes",
        "drivers": [
            {"name": "George Russell", "code": "RUS", "number": 63, "nat": "British", "ref": "russell"},
            {"name": "Andrea Kimi Antonelli", "code": "ANT", "number": 12, "nat": "Italian", "ref": "antonelli"},
        ],
    },
    {
        "team": "Aston Martin", "full_name": "Aston Martin Aramco F1 Team", "ref": "aston_martin",
        "drivers": [
            {"name": "Fernando Alonso", "code": "ALO", "number": 14, "nat": "Spanish", "ref": "alonso"},
            {"name": "Lance Stroll", "code": "STR", "number": 18, "nat": "Canadian", "ref": "stroll"},
        ],
    },
    {
        "team": "Alpine", "full_name": "BWT Alpine F1 Team", "ref": "alpine",
        "drivers": [
            {"name": "Pierre Gasly", "code": "GAS", "number": 10, "nat": "French", "ref": "gasly"},
            {"name": "Franco Colapinto", "code": "COL", "number": 43, "nat": "Argentine", "ref": "colapinto"},
        ],
    },
    {
        "team": "Williams", "full_name": "Williams Racing", "ref": "williams",
        "drivers": [
            {"name": "Alexander Albon", "code": "ALB", "number": 23, "nat": "Thai", "ref": "albon"},
            {"name": "Carlos Sainz", "code": "SAI", "number": 55, "nat": "Spanish", "ref": "sainz"},
        ],
    },
    {
        "team": "Racing Bulls", "full_name": "Visa Cash App Racing Bulls F1 Team", "ref": "rb",
        "drivers": [
            {"name": "Liam Lawson", "code": "LAW", "number": 30, "nat": "New Zealander", "ref": "lawson"},
            {"name": "Arvid Lindblad", "code": "LIN", "number": 41, "nat": "British", "ref": "arvid_lindblad"},
        ],
    },
    {
        "team": "Haas", "full_name": "MoneyGram Haas F1 Team", "ref": "haas",
        "drivers": [
            {"name": "Oliver Bearman", "code": "BEA", "number": 87, "nat": "British", "ref": "bearman"},
            {"name": "Rafael Camara", "code": "CAM", "number": 51, "nat": "Brazilian", "ref": "rafael_camara"},
        ],
    },
    {
        "team": "Audi", "full_name": "Audi F1 Team", "ref": "audi",
        "drivers": [
            {"name": "Nico Hulkenberg", "code": "HUL", "number": 27, "nat": "German", "ref": "hulkenberg"},
            {"name": "Gabriel Bortoleto", "code": "BOR", "number": 5, "nat": "Brazilian", "ref": "bortoleto"},
        ],
    },
    {
        "team": "Cadillac", "full_name": "Cadillac Formula 1 Team", "ref": "cadillac",
        "drivers": [
            {"name": "Sergio Perez", "code": "PER", "number": 11, "nat": "Mexican", "ref": "perez"},
            {"name": "Valtteri Bottas", "code": "BOT", "number": 77, "nat": "Finnish", "ref": "bottas"},
        ],
    },
]


# The 2026 calendar as actually run: 23 rounds.
#
# Bahrain and Saudi Arabia were cancelled in the spring; a replacement
# "Bahrain Grand Prix in Malaysia" was later added at Sepang as round 16, so
# every round from Singapore on is numbered one higher than the mid-season
# 22-round schedule. Times are the scheduled starts in UTC.
#
# Sprint weekends: China, Miami, Canada, Great Britain, Netherlands, Singapore.
CALENDAR_2026: list[dict] = [
    {"round": 1, "name": "Australian Grand Prix", "start": "2026-03-08T04:00", "sprint": None, "circuit_ref": "albert_park", "circuit": "Albert Park Grand Prix Circuit", "city": "Melbourne", "country": "Australia", "lat": -37.8497, "lon": 144.968},
    {"round": 2, "name": "Chinese Grand Prix", "start": "2026-03-15T07:00", "sprint": "2026-03-14T03:00", "circuit_ref": "shanghai", "circuit": "Shanghai International Circuit", "city": "Shanghai", "country": "China", "lat": 31.3389, "lon": 121.22},
    {"round": 3, "name": "Japanese Grand Prix", "start": "2026-03-29T05:00", "sprint": None, "circuit_ref": "suzuka", "circuit": "Suzuka Circuit", "city": "Suzuka", "country": "Japan", "lat": 34.8431, "lon": 136.541},
    {"round": 4, "name": "Miami Grand Prix", "start": "2026-05-03T20:00", "sprint": "2026-05-02T16:00", "circuit_ref": "miami", "circuit": "Miami International Autodrome", "city": "Miami", "country": "USA", "lat": 25.9581, "lon": -80.2389},
    {"round": 5, "name": "Canadian Grand Prix", "start": "2026-05-24T20:00", "sprint": "2026-05-23T16:00", "circuit_ref": "villeneuve", "circuit": "Circuit Gilles Villeneuve", "city": "Montreal", "country": "Canada", "lat": 45.5, "lon": -73.5228},
    {"round": 6, "name": "Monaco Grand Prix", "start": "2026-06-07T13:00", "sprint": None, "circuit_ref": "monaco", "circuit": "Circuit de Monaco", "city": "Monte Carlo", "country": "Monaco", "lat": 43.7347, "lon": 7.42056},
    {"round": 7, "name": "Barcelona Grand Prix", "start": "2026-06-14T13:00", "sprint": None, "circuit_ref": "catalunya", "circuit": "Circuit de Barcelona-Catalunya", "city": "Barcelona", "country": "Spain", "lat": 41.57, "lon": 2.26111},
    {"round": 8, "name": "Austrian Grand Prix", "start": "2026-06-28T13:00", "sprint": None, "circuit_ref": "red_bull_ring", "circuit": "Red Bull Ring", "city": "Spielberg", "country": "Austria", "lat": 47.2197, "lon": 14.7647},
    {"round": 9, "name": "British Grand Prix", "start": "2026-07-05T14:00", "sprint": "2026-07-04T11:00", "circuit_ref": "silverstone", "circuit": "Silverstone Circuit", "city": "Silverstone", "country": "UK", "lat": 52.0786, "lon": -1.01694},
    {"round": 10, "name": "Belgian Grand Prix", "start": "2026-07-19T13:00", "sprint": None, "circuit_ref": "spa", "circuit": "Circuit de Spa-Francorchamps", "city": "Spa", "country": "Belgium", "lat": 50.4372, "lon": 5.97139},
    {"round": 11, "name": "Hungarian Grand Prix", "start": "2026-07-26T13:00", "sprint": None, "circuit_ref": "hungaroring", "circuit": "Hungaroring", "city": "Budapest", "country": "Hungary", "lat": 47.5789, "lon": 19.2486},
    {"round": 12, "name": "Dutch Grand Prix", "start": "2026-08-23T13:00", "sprint": "2026-08-22T10:00", "circuit_ref": "zandvoort", "circuit": "Circuit Park Zandvoort", "city": "Zandvoort", "country": "Netherlands", "lat": 52.3888, "lon": 4.54092},
    {"round": 13, "name": "Italian Grand Prix", "start": "2026-09-06T13:00", "sprint": None, "circuit_ref": "monza", "circuit": "Autodromo Nazionale di Monza", "city": "Monza", "country": "Italy", "lat": 45.6156, "lon": 9.28111},
    {"round": 14, "name": "Spanish Grand Prix", "start": "2026-09-13T13:00", "sprint": None, "circuit_ref": "madring", "circuit": "Madring", "city": "Madrid", "country": "Spain", "lat": 40.46528, "lon": -3.61528},
    {"round": 15, "name": "Azerbaijan Grand Prix", "start": "2026-09-26T11:00", "sprint": None, "circuit_ref": "baku", "circuit": "Baku City Circuit", "city": "Baku", "country": "Azerbaijan", "lat": 40.3725, "lon": 49.8533},
    {"round": 16, "name": "Bahrain Grand Prix in Malaysia", "start": "2026-10-04T07:00", "sprint": None, "circuit_ref": "sepang", "circuit": "Sepang International Circuit", "city": "Kuala Lumpur", "country": "Malaysia", "lat": 2.76083, "lon": 101.738},
    {"round": 17, "name": "Singapore Grand Prix", "start": "2026-10-11T12:00", "sprint": "2026-10-10T09:00", "circuit_ref": "marina_bay", "circuit": "Marina Bay Street Circuit", "city": "Marina Bay", "country": "Singapore", "lat": 1.2914, "lon": 103.864},
    {"round": 18, "name": "United States Grand Prix", "start": "2026-10-25T20:00", "sprint": None, "circuit_ref": "americas", "circuit": "Circuit of the Americas", "city": "Austin", "country": "USA", "lat": 30.1328, "lon": -97.6411},
    {"round": 19, "name": "Mexico City Grand Prix", "start": "2026-11-01T20:00", "sprint": None, "circuit_ref": "rodriguez", "circuit": "Autódromo Hermanos Rodríguez", "city": "Mexico City", "country": "Mexico", "lat": 19.4042, "lon": -99.0907},
    {"round": 20, "name": "Brazilian Grand Prix", "start": "2026-11-08T17:00", "sprint": None, "circuit_ref": "interlagos", "circuit": "Autódromo José Carlos Pace", "city": "São Paulo", "country": "Brazil", "lat": -23.7036, "lon": -46.6997},
    {"round": 21, "name": "Las Vegas Grand Prix", "start": "2026-11-22T04:00", "sprint": None, "circuit_ref": "vegas", "circuit": "Las Vegas Strip Street Circuit", "city": "Las Vegas", "country": "USA", "lat": 36.1147, "lon": -115.173},
    {"round": 22, "name": "Qatar Grand Prix", "start": "2026-11-29T16:00", "sprint": None, "circuit_ref": "losail", "circuit": "Lusail International Circuit", "city": "Lusail", "country": "Qatar", "lat": 25.49, "lon": 51.4542},
    {"round": 23, "name": "Abu Dhabi Grand Prix", "start": "2026-12-06T13:00", "sprint": None, "circuit_ref": "yas_marina", "circuit": "Yas Marina Circuit", "city": "Abu Dhabi", "country": "UAE", "lat": 24.4672, "lon": 54.6031},
]


def _utc(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)


async def upsert_team(session, name: str, full_name: str, ref: str | None = None) -> Team:
    team = None
    if ref:
        team = (await session.execute(select(Team).where(Team.ref == ref))).scalar_one_or_none()
    if team is None:
        team = (await session.execute(select(Team).where(Team.name == name))).scalar_one_or_none()
    if team is None:
        team = Team(name=name, full_name=full_name, ref=ref, is_active=True)
        session.add(team)
        await session.flush()
        logger.info(f"Created team {name}")
    else:
        team.name = name
        team.full_name = full_name
        team.ref = ref or team.ref
        team.is_active = True
    return team


async def upsert_driver(session, team: Team, d: dict) -> Driver:
    driver = None
    if d.get("ref"):
        driver = (
            await session.execute(select(Driver).where(Driver.ref == d["ref"]))
        ).scalar_one_or_none()
    if driver is None:
        driver = (
            await session.execute(select(Driver).where(Driver.code == d["code"]))
        ).scalar_one_or_none()
    if driver is None:
        driver = Driver(
            name=d["name"],
            code=d["code"],
            ref=d.get("ref"),
            number=d["number"],
            nationality=d["nat"],
            team_id=team.id,
            is_active=True,
        )
        session.add(driver)
        logger.info(f"Created driver {d['code']} ({d['name']})")
    else:
        driver.name = d["name"]
        driver.ref = d.get("ref") or driver.ref
        driver.number = d["number"]
        driver.nationality = d["nat"]
        driver.team_id = team.id
        driver.is_active = True
    return driver


async def upsert_circuit(
    session,
    ref: str | None,
    name: str,
    city: str,
    country: str,
    lat: float | None = None,
    lon: float | None = None,
) -> Circuit:
    """Find a circuit by its API ref, then by name; create it otherwise.

    Circuits are venue-specific. An earlier version matched on country alone,
    which collapsed Miami, Austin and Las Vegas into one row.
    """
    circuit = None
    if ref:
        circuit = (
            await session.execute(select(Circuit).where(Circuit.ref == ref))
        ).scalar_one_or_none()
    if circuit is None and name:
        circuit = (
            await session.execute(select(Circuit).where(Circuit.name == name))
        ).scalar_one_or_none()
    if circuit is None:
        circuit = Circuit(
            name=name or "Unknown Circuit",
            ref=ref or None,
            country=country or "Unknown",
            city=city or country or "Unknown",
            track_type=TrackType.mixed,
            latitude=lat or None,
            longitude=lon or None,
        )
        session.add(circuit)
        await session.flush()
        logger.info(f"Created circuit {name}")
    else:
        circuit.ref = ref or circuit.ref
        circuit.city = city or circuit.city
        circuit.country = country or circuit.country
        circuit.latitude = lat or circuit.latitude
        circuit.longitude = lon or circuit.longitude
    return circuit


async def upsert_race(
    session,
    season: int,
    round_number: int,
    *,
    name: str,
    start: datetime,
    circuit: Circuit,
    sprint_start: datetime | None = None,
    qualifying_start: datetime | None = None,
) -> tuple[Race, bool]:
    """Create or update a race by (season, round). Returns (race, created)."""
    race = (
        await session.execute(
            select(Race).where(Race.season == season, Race.round_number == round_number)
        )
    ).scalar_one_or_none()
    created = race is None
    if race is None:
        race = Race(
            season=season,
            round_number=round_number,
            race_name=name,
            race_date=start,
            circuit_id=circuit.id,
            status=RaceStatus.scheduled,
        )
        session.add(race)
    moved = race.race_date != start
    race.race_name = name
    race.race_date = start
    race.circuit_id = circuit.id
    race.is_sprint_weekend = sprint_start is not None
    race.sprint_date = sprint_start
    race.qualifying_date = qualifying_start or race.qualifying_date
    # The pool takes bets until the scheduled start. Keep a deadline that was
    # set from the pool's own calendar unless the race itself moved.
    if race.deadline_bets is None or moved:
        race.deadline_bets = start
    if race.status == RaceStatus.cancelled:
        race.status = RaceStatus.scheduled
    await session.flush()
    return race, created


async def seed_calendar(session, season: int = 2026) -> int:
    """Create/refresh the season's races from the static calendar."""
    created = 0
    for gp in CALENDAR_2026:
        circuit = await upsert_circuit(
            session, gp["circuit_ref"], gp["circuit"], gp["city"], gp["country"],
            gp["lat"], gp["lon"],
        )
        _, was_created = await upsert_race(
            session, season, gp["round"],
            name=gp["name"], start=_utc(gp["start"]), circuit=circuit,
            sprint_start=_utc(gp["sprint"]),
        )
        created += int(was_created)
    return created


async def seed() -> None:
    async with AsyncSessionLocal() as session:
        for entry in GRID:
            team = await upsert_team(session, entry["team"], entry["full_name"], entry["ref"])
            for d in entry["drivers"]:
                await upsert_driver(session, team, d)
        created = await seed_calendar(session)
        await session.commit()
    logger.info(
        f"Seed complete: {len(GRID)} teams, {sum(len(e['drivers']) for e in GRID)} drivers, "
        f"{created} new 2026 races"
    )


if __name__ == "__main__":
    asyncio.run(seed())
