"""F1 results collector – Jolpica (the Ergast successor) and OpenF1.

Jolpica is the system's primary source of race, sprint and qualifying data.

Three things that matter in practice:

* **Status vocabulary.** Jolpica reports classified finishers as ``Finished``
  or ``Lapped`` (2026) — the Ergast-era ``+1 Lap`` strings are gone. A parser
  that only knows the old strings records every lapped finisher as a
  retirement: 129 rows in 2026 alone.
* **Rate limits.** 4 requests/second and 500/hour. Fetching one race at a
  time costs ~4 requests per round (≈440 for five seasons), so the season
  endpoints are used instead: a whole season of results is 5 paginated
  requests.
* **Pagination.** Endpoints default to 30 rows. Everything here asks for the
  maximum page size (100) and follows ``MRData.total``.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone

import httpx
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

_FINISHED = re.compile(r"^(Finished|Lapped|\+\d+ Laps?)$")
_NOT_STARTED = {"Did not start", "Withdrew", "Did not qualify", "Did not prequalify"}


def _retryable(exc: BaseException) -> bool:
    """Retry rate limits, server errors and transport failures — not 404s."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


class F1ApiFallback:
    """Collector for the Jolpica (Ergast-compatible) and OpenF1 APIs."""

    # ergast.com was frozen after the 2024 season. Jolpica is the drop-in
    # successor: identical JSON shape and paths, only the base URL changes.
    BASE_URL_ERGAST = "https://api.jolpi.ca/ergast/f1"
    BASE_URL_OPENF1 = "https://api.openf1.org/v1"

    PAGE_LIMIT = 100               # the API's maximum page size
    MIN_REQUEST_INTERVAL = 0.35    # stay under the 4 req/s burst limit

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._last_request_at = 0.0
        self._rate_lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                follow_redirects=True,
                headers={"User-Agent": "bf1-quant/1.0"},
            )
        return self._client

    async def _rate_limit(self) -> None:
        async with self._rate_lock:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self.MIN_REQUEST_INTERVAL:
                await asyncio.sleep(self.MIN_REQUEST_INTERVAL - elapsed)
            self._last_request_at = time.monotonic()

    @retry(
        retry=retry_if_exception(_retryable),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        reraise=True,
    )
    async def _fetch_json(self, url: str):
        await self._rate_limit()
        client = await self._get_client()
        logger.debug(f"F1 API fetching {url}")
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()

    def _paged(self, path: str, offset: int = 0) -> str:
        sep = "&" if "?" in path else "?"
        url = f"{path}{sep}limit={self.PAGE_LIMIT}"
        return f"{url}&offset={offset}" if offset else url

    async def _fetch_races(self, path: str, list_key: str | None = None) -> list[dict]:
        """Fetch every page of a RaceTable endpoint, merging rounds split across pages."""
        races: dict[str, dict] = {}
        offset, total = 0, None
        while total is None or offset < total:
            data = (await self._fetch_json(self._paged(path, offset))).get("MRData", {})
            total = int(data.get("total", 0))
            for race in data.get("RaceTable", {}).get("Races", []):
                key = f"{race.get('season')}-{race.get('round')}"
                if key not in races:
                    races[key] = race
                elif list_key:
                    races[key].setdefault(list_key, []).extend(race.get(list_key, []))
            offset += self.PAGE_LIMIT
        return sorted(races.values(), key=lambda r: (int(r["season"]), int(r["round"])))

    # ------------------------------------------------------------------ #
    # Parsing
    # ------------------------------------------------------------------ #
    @staticmethod
    def _is_dnf(status: str) -> bool:
        """True when the driver was not a classified finisher.

        ``Finished``, ``Lapped`` and the Ergast-era ``+N Lap(s)`` all mean the
        car took the flag. Anything else — ``Retired``, ``Did not start``,
        ``Disqualified`` — did not score.
        """
        return _FINISHED.match((status or "").strip()) is None

    @staticmethod
    def _is_retirement(status: str) -> bool:
        """True when the pool charges its retirement penalty.

        A car that never started is not a retirement: checked against the
        pool's official scores, which do not penalise a DNS.
        """
        s = (status or "").strip()
        return F1ApiFallback._is_dnf(s) and s not in _NOT_STARTED

    @classmethod
    def _parse_result(cls, row: dict) -> dict:
        driver = row.get("Driver", {})
        constructor = row.get("Constructor", {})
        status = row.get("status", "")
        dnf = cls._is_dnf(status)
        pos = str(row.get("position", ""))
        family = driver.get("familyName", "")
        return {
            "position": int(pos) if pos.isdigit() and not dnf else None,
            "position_text": row.get("positionText", ""),
            "driver": f"{driver.get('givenName', '')} {family}".strip(),
            "driver_code": driver.get("code") or family[:3].upper(),
            "driver_ref": driver.get("driverId", ""),
            "driver_number": int(driver.get("permanentNumber") or row.get("number") or 0),
            "nationality": driver.get("nationality", ""),
            "team": constructor.get("name", ""),
            "team_ref": constructor.get("constructorId", ""),
            "grid": int(row.get("grid", 0) or 0),
            "laps": int(row.get("laps", 0) or 0),
            "points": float(row.get("points", 0) or 0),
            "status": status,
            "dnf": dnf,
            "retired": cls._is_retirement(status),
            "dnf_reason": status if dnf else None,
            "fastest_lap": row.get("FastestLap", {}).get("rank") == "1",
        }

    @classmethod
    def _parse_qualifying(cls, row: dict) -> dict:
        driver = row.get("Driver", {})
        constructor = row.get("Constructor", {})
        family = driver.get("familyName", "")
        return {
            "position": int(row.get("position", 0) or 0),
            "driver": f"{driver.get('givenName', '')} {family}".strip(),
            "driver_code": driver.get("code") or family[:3].upper(),
            "driver_ref": driver.get("driverId", ""),
            "driver_number": int(driver.get("permanentNumber") or row.get("number") or 0),
            "nationality": driver.get("nationality", ""),
            "team": constructor.get("name", ""),
            "team_ref": constructor.get("constructorId", ""),
            "q1_time": cls._parse_ergast_time(row.get("Q1")),
            "q2_time": cls._parse_ergast_time(row.get("Q2")),
            "q3_time": cls._parse_ergast_time(row.get("Q3")),
        }

    @staticmethod
    def _parse_start(date: str | None, clock: str | None) -> datetime | None:
        """Combine Jolpica's ``date`` and ``time`` ("07:00:00Z") into UTC."""
        if not date:
            return None
        try:
            if clock:
                return datetime.strptime(
                    f"{date[:10]}T{clock.rstrip('Z')}", "%Y-%m-%dT%H:%M:%S"
                ).replace(tzinfo=timezone.utc)
            return datetime.strptime(date[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    @classmethod
    def _parse_calendar_race(cls, race: dict) -> dict:
        circuit = race.get("Circuit", {})
        loc = circuit.get("Location", {})
        sprint = race.get("Sprint") or {}
        quali = race.get("Qualifying") or {}
        return {
            "season": int(race.get("season", 0) or 0),
            "round": int(race.get("round", 0) or 0),
            "race_name": race.get("raceName", ""),
            "circuit": circuit.get("circuitName", ""),
            "circuit_ref": circuit.get("circuitId", ""),
            "country": loc.get("country", ""),
            "locality": loc.get("locality", ""),
            "date": race.get("date", ""),
            "start": cls._parse_start(race.get("date"), race.get("time")),
            # Sprint weekends carry a separate "Sprint" object. The race name
            # is always "<Country> Grand Prix" and never contains the word.
            "is_sprint": bool(sprint),
            "sprint_start": cls._parse_start(sprint.get("date"), sprint.get("time")),
            "qualifying_start": cls._parse_start(quali.get("date"), quali.get("time")),
            "latitude": float(loc.get("lat", 0) or 0),
            "longitude": float(loc.get("long", 0) or 0),
        }

    @staticmethod
    def _parse_ergast_time(time_str: str | None) -> float | None:
        """Parse a lap time such as '1:23.456' into seconds."""
        if not time_str:
            return None
        try:
            parts = time_str.split(":")
            if len(parts) == 2:
                return int(parts[0]) * 60 + float(parts[1])
            return float(time_str)
        except (ValueError, IndexError):
            return None

    # ------------------------------------------------------------------ #
    # Season-level fetches (preferred: a handful of requests per season)
    # ------------------------------------------------------------------ #
    async def fetch_calendar(self, season: int) -> list[dict]:
        """The season's calendar with real start times and sprint flags."""
        try:
            races = await self._fetch_races(f"{self.BASE_URL_ERGAST}/{season}.json")
            parsed = [self._parse_calendar_race(r) for r in races]
            logger.info(f"Jolpica: fetched {len(parsed)} races for {season}")
            return parsed
        except Exception as e:
            logger.warning(f"Jolpica calendar failed for {season}: {e}")
            return await self._fetch_calendar_openf1(season)

    async def _fetch_calendar_openf1(self, season: int) -> list[dict]:
        """Fallback calendar from OpenF1 (no sprint flags, dates only)."""
        url = f"{self.BASE_URL_OPENF1}/sessions?year={season}&session_type=Race"
        try:
            data = await self._fetch_json(url)
            if not isinstance(data, list):
                return []
            mains = [s for s in data if s.get("session_name") == "Race"]
            races = []
            for idx, session in enumerate(mains, 1):
                start = None
                try:
                    start = datetime.fromisoformat(session.get("date_start", ""))
                except ValueError:
                    pass
                races.append(
                    {
                        "season": season,
                        "round": idx,
                        "race_name": f"{session.get('country_name', '')} Grand Prix",
                        "circuit": session.get("circuit_short_name", ""),
                        "circuit_ref": "",
                        "country": session.get("country_name", ""),
                        "locality": session.get("location", ""),
                        "date": (session.get("date_start") or "")[:10],
                        "start": start,
                        "is_sprint": False,
                        "sprint_start": None,
                        "qualifying_start": None,
                        "latitude": 0.0,
                        "longitude": 0.0,
                    }
                )
            logger.info(f"OpenF1: fetched {len(races)} races for {season}")
            return races
        except Exception as e:
            logger.error(f"OpenF1 calendar also failed for {season}: {e}")
            return []

    async def fetch_season_results(self, season: int) -> dict[int, list[dict]]:
        """{round: [result rows]} for every finished race of the season."""
        races = await self._fetch_races(
            f"{self.BASE_URL_ERGAST}/{season}/results.json", "Results"
        )
        return {
            int(r["round"]): [self._parse_result(x) for x in r.get("Results", [])]
            for r in races
        }

    async def fetch_season_sprints(self, season: int) -> dict[int, list[dict]]:
        """{round: [sprint result rows]} for every sprint of the season."""
        races = await self._fetch_races(
            f"{self.BASE_URL_ERGAST}/{season}/sprint.json", "SprintResults"
        )
        return {
            int(r["round"]): [self._parse_result(x) for x in r.get("SprintResults", [])]
            for r in races
        }

    async def fetch_season_qualifying(self, season: int) -> dict[int, list[dict]]:
        """{round: [qualifying rows]} for the season."""
        races = await self._fetch_races(
            f"{self.BASE_URL_ERGAST}/{season}/qualifying.json", "QualifyingResults"
        )
        return {
            int(r["round"]): [
                self._parse_qualifying(x) for x in r.get("QualifyingResults", [])
            ]
            for r in races
        }

    # ------------------------------------------------------------------ #
    # Single-round fetches
    # ------------------------------------------------------------------ #
    async def _one_round(self, season: int, round_num: int, kind: str, key: str) -> list[dict]:
        races = await self._fetch_races(
            f"{self.BASE_URL_ERGAST}/{season}/{round_num}/{kind}.json", key
        )
        return races[0].get(key, []) if races else []

    async def fetch_race_results(self, season: int, round_num: int) -> list[dict]:
        try:
            rows = await self._one_round(season, round_num, "results", "Results")
            return [self._parse_result(x) for x in rows]
        except Exception as e:
            logger.warning(f"Jolpica results failed for {season}/R{round_num}: {e}")
            return []

    async def fetch_sprint_results(self, season: int, round_num: int) -> list[dict]:
        try:
            rows = await self._one_round(season, round_num, "sprint", "SprintResults")
            return [self._parse_result(x) for x in rows]
        except Exception as e:
            logger.warning(f"Jolpica sprint failed for {season}/R{round_num}: {e}")
            return []

    async def fetch_qualifying_results(self, season: int, round_num: int) -> list[dict]:
        try:
            rows = await self._one_round(
                season, round_num, "qualifying", "QualifyingResults"
            )
            return [self._parse_qualifying(x) for x in rows]
        except Exception as e:
            logger.warning(f"Jolpica qualifying failed for {season}/R{round_num}: {e}")
            return []

    async def fetch_pit_stops(self, season: int, round_num: int) -> list[dict]:
        """Every pit stop of a race (a modern GP has 35-60, i.e. one page)."""
        try:
            rows = await self._one_round(season, round_num, "pitstops", "PitStops")
            return [
                {
                    "driver": p.get("driverId", ""),
                    "stop_number": int(p.get("stop", 0) or 0),
                    "lap": int(p.get("lap", 0) or 0),
                    "duration": p.get("duration", ""),
                }
                for p in rows
            ]
        except Exception as e:
            logger.warning(f"Jolpica pit stops failed for {season}/R{round_num}: {e}")
            return []

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


async def get_collector() -> F1ApiFallback:
    """Factory that returns the API collector."""
    return F1ApiFallback()
