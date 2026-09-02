"""F1 API fallback – Jolpica (Ergast-compatible) and OpenF1 as data source."""

from __future__ import annotations

import asyncio
import re
import time

import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings


class F1ApiFallback:
    """
    Fallback data collector using the Jolpica and OpenF1 APIs.
    Provides the same interface as F1DataCollector.
    """

    # ergast.com was frozen after the 2024 season and the host no longer
    # serves current data. Jolpica is the drop-in successor: identical JSON
    # shape and path structure, so only the base URL changes.
    BASE_URL_ERGAST = "https://api.jolpi.ca/ergast/f1"
    BASE_URL_OPENF1 = "https://api.openf1.org/v1"

    # Ergast-compatible endpoints default to limit=30 and silently truncate.
    # A modern GP has ~50 pit stops, so the default cut them off mid-race.
    PAGE_LIMIT = 100
    # Jolpica publishes a 4 req/s burst limit; stay under it.
    MIN_REQUEST_INTERVAL = 0.35

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._last_request_at = 0.0
        self._rate_lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                follow_redirects=True,
            )
        return self._client

    async def _rate_limit(self) -> None:
        """Space out requests. Ingest issues ~4 calls per race back-to-back."""
        async with self._rate_lock:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self.MIN_REQUEST_INTERVAL:
                await asyncio.sleep(self.MIN_REQUEST_INTERVAL - elapsed)
            self._last_request_at = time.monotonic()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def _fetch_json(self, url: str) -> dict:
        await self._rate_limit()
        client = await self._get_client()
        logger.debug(f"API fallback fetching {url}")
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

        # Warn loudly if the server holds more rows than we asked for, rather
        # than silently analysing a truncated race.
        mrdata = data.get("MRData", {})
        try:
            total = int(mrdata.get("total", 0))
            limit = int(mrdata.get("limit", 0))
            offset = int(mrdata.get("offset", 0))
            if total > offset + limit:
                logger.warning(
                    f"Truncated response from {url}: {total} rows available, "
                    f"got {limit} at offset {offset}. Raise PAGE_LIMIT or page."
                )
        except (TypeError, ValueError):
            pass
        return data

    def _paged(self, path: str) -> str:
        """Build an Ergast-style URL with an explicit, non-default limit."""
        sep = "&" if "?" in path else "?"
        return f"{path}{sep}limit={self.PAGE_LIMIT}"

    @staticmethod
    def _is_dnf(status: str) -> bool:
        """True only for genuine non-finishes.

        A driver classified any number of laps down still finished. The old
        allowlist stopped at "+3 Laps", so "+4 Laps" and beyond were recorded
        as retirements *with* a valid finishing position — which then flowed
        into ELO (forced to P20), circuit DNF rates, team reliability and the
        XGBoost training label. Matching the "+N Lap(s)" pattern instead of
        enumerating it keeps classified finishers on the right side.
        """
        s = (status or "").strip()
        if s == "Finished":
            return False
        return re.fullmatch(r"\+\d+ Laps?", s) is None

    async def fetch_calendar(self, season: int) -> list[dict]:
        """Fetch race calendar from Ergast API."""
        url = self._paged(f"{self.BASE_URL_ERGAST}/{season}.json")
        try:
            data = await self._fetch_json(url)
            races_raw = (
                data.get("MRData", {}).get("RaceTable", {}).get("Races", [])
            )

            races: list[dict] = []
            for race in races_raw:
                races.append(
                    {
                        "round": int(race.get("round", 0)),
                        "race_name": race.get("raceName", ""),
                        "circuit": race.get("Circuit", {}).get(
                            "circuitName", ""
                        ),
                        "country": race.get("Circuit", {})
                        .get("Location", {})
                        .get("country", ""),
                        "date": race.get("date", ""),
                        # Sprint weekends are flagged by a separate "Sprint"
                        # object on the race, not by the race name: raceName is
                        # always "<Country> Grand Prix" and never contains the
                        # word "Sprint", so the old substring test was always
                        # False and sprint results were never ingested at all.
                        "is_sprint": bool(race.get("Sprint")),
                        "latitude": float(
                            race.get("Circuit", {})
                            .get("Location", {})
                            .get("lat", 0)
                        ),
                        "longitude": float(
                            race.get("Circuit", {})
                            .get("Location", {})
                            .get("long", 0)
                        ),
                    }
                )

            logger.info(f"Ergast: fetched {len(races)} races for {season}")
            return races
        except Exception as e:
            logger.warning(f"Ergast calendar failed for {season}: {e}")
            return await self._fetch_calendar_openf1(season)

    async def _fetch_calendar_openf1(self, season: int) -> list[dict]:
        """Fallback to OpenF1 for calendar."""
        url = f"{self.BASE_URL_OPENF1}/sessions?year={season}&session_type=Race"
        try:
            data = await self._fetch_json(url)
            if not isinstance(data, list):
                return []
            races = []
            for idx, session in enumerate(data, 1):
                races.append(
                    {
                        "round": idx,
                        "race_name": session.get("meeting_name", ""),
                        "circuit": session.get("circuit_short_name", ""),
                        "country": session.get("country_name", ""),
                        "date": session.get("date_start", ""),
                        "is_sprint": False,
                    }
                )
            logger.info(f"OpenF1: fetched {len(races)} races for {season}")
            return races
        except Exception as e:
            logger.error(f"OpenF1 calendar also failed for {season}: {e}")
            return []

    async def fetch_qualifying_results(
        self, season: int, round_num: int
    ) -> list[dict]:
        """Fetch qualifying results from Ergast API."""
        url = self._paged(f"{self.BASE_URL_ERGAST}/{season}/{round_num}/qualifying.json")
        try:
            data = await self._fetch_json(url)
            quali_raw = (
                data.get("MRData", {})
                .get("RaceTable", {})
                .get("Races", [{}])[0]
                .get("QualifyingResults", [])
            )

            results: list[dict] = []
            for q in quali_raw:
                driver = q.get("Driver", {})
                results.append(
                    {
                        "position": int(q.get("position", 0)),
                        "driver": f"{driver.get('givenName', '')} {driver.get('familyName', '')}",
                        "driver_code": driver.get("code", ""),
                        "q1_time": self._parse_ergast_time(q.get("Q1")),
                        "q2_time": self._parse_ergast_time(q.get("Q2")),
                        "q3_time": self._parse_ergast_time(q.get("Q3")),
                    }
                )

            logger.info(
                f"Ergast: fetched {len(results)} qualifying results for {season} R{round_num}"
            )
            return results
        except Exception as e:
            logger.warning(f"Ergast qualifying failed for {season}/R{round_num}: {e}")
            return []

    async def fetch_race_results(self, season: int, round_num: int) -> list[dict]:
        """Fetch race results from Ergast API."""
        url = self._paged(f"{self.BASE_URL_ERGAST}/{season}/{round_num}/results.json")
        try:
            data = await self._fetch_json(url)
            results_raw = (
                data.get("MRData", {})
                .get("RaceTable", {})
                .get("Races", [{}])[0]
                .get("Results", [])
            )

            results: list[dict] = []
            for r in results_raw:
                driver = r.get("Driver", {})
                constructor = r.get("Constructor", {})
                status = r.get("status", "")
                dnf = self._is_dnf(status)

                pos_text = r.get("position", "")
                position = int(pos_text) if pos_text.isdigit() else None

                results.append(
                    {
                        "position": position,
                        "driver": f"{driver.get('givenName', '')} {driver.get('familyName', '')}",
                        "driver_code": driver.get("code", ""),
                        "driver_ref": driver.get("driverId", ""),
                        "team": constructor.get("name", ""),
                        "grid": int(r.get("grid", 0)),
                        "laps": int(r.get("laps", 0)),
                        "points": float(r.get("points", 0)),
                        "dnf": dnf,
                        "dnf_reason": status if dnf else None,
                        "fastest_lap": r.get("FastestLap", {}).get("rank") == "1",
                    }
                )

            logger.info(
                f"Ergast: fetched {len(results)} race results for {season} R{round_num}"
            )
            return results
        except Exception as e:
            logger.warning(f"Ergast results failed for {season}/R{round_num}: {e}")
            return []

    async def fetch_sprint_results(self, season: int, round_num: int) -> list[dict]:
        """Fetch sprint results from Ergast API."""
        url = self._paged(f"{self.BASE_URL_ERGAST}/{season}/{round_num}/sprint.json")
        try:
            data = await self._fetch_json(url)
            sprint_raw = (
                data.get("MRData", {})
                .get("RaceTable", {})
                .get("Races", [{}])[0]
                .get("SprintResults", [])
            )

            results: list[dict] = []
            for r in sprint_raw:
                driver = r.get("Driver", {})
                status = r.get("status", "")
                dnf = self._is_dnf(status)
                pos_text = r.get("position", "")

                results.append(
                    {
                        "position": int(pos_text) if pos_text.isdigit() else None,
                        "driver": f"{driver.get('givenName', '')} {driver.get('familyName', '')}",
                        "driver_code": driver.get("code", ""),
                        "points": float(r.get("points", 0)),
                        "dnf": dnf,
                    }
                )

            logger.info(
                f"Ergast: fetched {len(results)} sprint results for {season} R{round_num}"
            )
            return results
        except Exception as e:
            logger.warning(f"Ergast sprint failed for {season}/R{round_num}: {e}")
            return []

    async def fetch_pit_stops(self, season: int, round_num: int) -> list[dict]:
        """Fetch pit stop data from Ergast API."""
        url = self._paged(f"{self.BASE_URL_ERGAST}/{season}/{round_num}/pitstops.json")
        try:
            data = await self._fetch_json(url)
            pits_raw = (
                data.get("MRData", {})
                .get("RaceTable", {})
                .get("Races", [{}])[0]
                .get("PitStops", [])
            )

            stops: list[dict] = []
            for p in pits_raw:
                stops.append(
                    {
                        "driver": p.get("driverId", ""),
                        "stop_number": int(p.get("stop", 0)),
                        "lap": int(p.get("lap", 0)),
                        "duration": p.get("duration", ""),
                    }
                )

            logger.info(
                f"Ergast: fetched {len(stops)} pit stops for {season} R{round_num}"
            )
            return stops
        except Exception as e:
            logger.warning(f"Ergast pit stops failed for {season}/R{round_num}: {e}")
            return []

    async def fetch_fastest_laps(self, season: int, round_num: int) -> list[dict]:
        """Fetch fastest lap data from race results."""
        results = await self.fetch_race_results(season, round_num)
        return [
            {
                "driver": r["driver"],
                "driver_code": r.get("driver_code", ""),
                "fastest_lap": r.get("fastest_lap", False),
            }
            for r in results
        ]

    async def fetch_historical_data(
        self, circuit_name: str, years: int = 5
    ) -> dict:
        """Fetch historical data for a circuit from APIs."""
        from datetime import datetime

        current_year = datetime.now().year
        data: dict = {
            "circuit": circuit_name,
            "years_covered": [],
            "race_results": [],
            "qualifying_results": [],
            "pit_stops": [],
        }

        for year in range(current_year - years, current_year):
            calendar = await self.fetch_calendar(year)
            for race in calendar:
                if circuit_name.lower() in race.get("circuit", "").lower() or \
                   circuit_name.lower() in race.get("race_name", "").lower():
                    round_num = race["round"]
                    data["years_covered"].append(year)

                    results = await self.fetch_race_results(year, round_num)
                    data["race_results"].extend(
                        [{**r, "season": year} for r in results]
                    )

                    quali = await self.fetch_qualifying_results(year, round_num)
                    data["qualifying_results"].extend(
                        [{**q, "season": year} for q in quali]
                    )

                    pits = await self.fetch_pit_stops(year, round_num)
                    data["pit_stops"].extend([{**p, "season": year} for p in pits])
                    break

        logger.info(
            f"API fallback: historical data for {circuit_name}: "
            f"{len(data['years_covered'])} years"
        )
        return data

    @staticmethod
    def _parse_ergast_time(time_str: str | None) -> float | None:
        """Parse Ergast time format (e.g., '1:23.456') to seconds."""
        if not time_str:
            return None
        try:
            parts = time_str.split(":")
            if len(parts) == 2:
                return int(parts[0]) * 60 + float(parts[1])
            return float(time_str)
        except (ValueError, IndexError):
            return None

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


async def get_collector() -> F1ApiFallback:
    """Factory that returns an API fallback collector."""
    return F1ApiFallback()
