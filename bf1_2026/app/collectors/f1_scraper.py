"""F1 data scraper – collects race data from official sources."""

from __future__ import annotations

import asyncio
import random
from datetime import datetime

import httpx
from bs4 import BeautifulSoup
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/119.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/118.0.0.0",
]


class F1DataCollector:
    """
    Scraper for F1 race data.

    Uses httpx async, BeautifulSoup, retry with exponential backoff,
    rate limiting (1 req/s), and rotating user agents.
    """

    BASE_URL = "https://www.formula1.com"

    def __init__(self) -> None:
        self._last_request_time: float = 0
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                follow_redirects=True,
                headers={"User-Agent": random.choice(USER_AGENTS)},
            )
        return self._client

    async def _rate_limit(self) -> None:
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request_time
        delay = settings.scraping_delay_seconds
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        self._last_request_time = asyncio.get_event_loop().time()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def _fetch_page(self, url: str) -> str:
        await self._rate_limit()
        client = await self._get_client()
        headers = {"User-Agent": random.choice(USER_AGENTS)}
        logger.debug(f"Fetching {url}")
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.text

    async def fetch_calendar(self, season: int) -> list[dict]:
        """Return list of races for a season."""
        url = f"{self.BASE_URL}/en/racing/{season}.html"
        try:
            html = await self._fetch_page(url)
            soup = BeautifulSoup(html, "lxml")

            races: list[dict] = []
            race_items = soup.select(".race-item, .event-item, [class*='race']")

            for idx, item in enumerate(race_items, 1):
                title_el = item.select_one(
                    ".race-title, .event-title, h3, h4, [class*='title']"
                )
                date_el = item.select_one(
                    ".race-date, .event-date, time, [class*='date']"
                )
                location_el = item.select_one(
                    ".race-location, .event-location, [class*='location'], [class*='country']"
                )

                race_name = title_el.get_text(strip=True) if title_el else f"Round {idx}"
                date_str = date_el.get_text(strip=True) if date_el else ""
                country = (
                    location_el.get_text(strip=True) if location_el else "Unknown"
                )

                is_sprint = "sprint" in race_name.lower()

                races.append(
                    {
                        "round": idx,
                        "race_name": race_name,
                        "circuit": race_name,
                        "country": country,
                        "date": date_str,
                        "is_sprint": is_sprint,
                    }
                )

            logger.info(f"Fetched {len(races)} races for {season}")
            return races
        except Exception as e:
            logger.warning(f"Scraper failed for calendar {season}: {e}")
            return []

    async def fetch_qualifying_results(
        self, season: int, round_num: int
    ) -> list[dict]:
        """Return qualifying grid with Q1/Q2/Q3 times."""
        url = f"{self.BASE_URL}/en/results/{season}/races/{round_num}/qualifying.html"
        try:
            html = await self._fetch_page(url)
            soup = BeautifulSoup(html, "lxml")

            results: list[dict] = []
            rows = soup.select("table tbody tr")

            for row in rows:
                cols = row.select("td")
                if len(cols) < 5:
                    continue

                position = cols[0].get_text(strip=True)
                driver_name = cols[1].get_text(strip=True)
                q1 = cols[2].get_text(strip=True) if len(cols) > 2 else None
                q2 = cols[3].get_text(strip=True) if len(cols) > 3 else None
                q3 = cols[4].get_text(strip=True) if len(cols) > 4 else None

                results.append(
                    {
                        "position": int(position) if position.isdigit() else None,
                        "driver": driver_name,
                        "q1_time": self._parse_lap_time(q1),
                        "q2_time": self._parse_lap_time(q2),
                        "q3_time": self._parse_lap_time(q3),
                    }
                )

            logger.info(
                f"Fetched {len(results)} qualifying results for {season} R{round_num}"
            )
            return results
        except Exception as e:
            logger.warning(f"Scraper failed for qualifying {season}/R{round_num}: {e}")
            return []

    async def fetch_race_results(self, season: int, round_num: int) -> list[dict]:
        """Return final race results with positions, points, DNFs, pit stops."""
        url = f"{self.BASE_URL}/en/results/{season}/races/{round_num}/race-result.html"
        try:
            html = await self._fetch_page(url)
            soup = BeautifulSoup(html, "lxml")

            results: list[dict] = []
            rows = soup.select("table tbody tr")

            for row in rows:
                cols = row.select("td")
                if len(cols) < 5:
                    continue

                pos_text = cols[0].get_text(strip=True)
                driver_name = cols[1].get_text(strip=True)
                team = cols[2].get_text(strip=True) if len(cols) > 2 else ""
                laps = cols[3].get_text(strip=True) if len(cols) > 3 else "0"
                time_str = cols[4].get_text(strip=True) if len(cols) > 4 else ""
                points = cols[5].get_text(strip=True) if len(cols) > 5 else "0"

                dnf = not pos_text.isdigit()
                position = int(pos_text) if pos_text.isdigit() else None

                results.append(
                    {
                        "position": position,
                        "driver": driver_name,
                        "team": team,
                        "laps": int(laps) if laps.isdigit() else 0,
                        "time": time_str,
                        "points": float(points) if points.replace(".", "").isdigit() else 0,
                        "dnf": dnf,
                        "dnf_reason": time_str if dnf else None,
                    }
                )

            logger.info(
                f"Fetched {len(results)} race results for {season} R{round_num}"
            )
            return results
        except Exception as e:
            logger.warning(f"Scraper failed for race results {season}/R{round_num}: {e}")
            return []

    async def fetch_sprint_results(self, season: int, round_num: int) -> list[dict]:
        """Return sprint race results (if applicable)."""
        url = f"{self.BASE_URL}/en/results/{season}/races/{round_num}/sprint-results.html"
        try:
            html = await self._fetch_page(url)
            soup = BeautifulSoup(html, "lxml")

            results: list[dict] = []
            rows = soup.select("table tbody tr")

            for row in rows:
                cols = row.select("td")
                if len(cols) < 4:
                    continue

                pos_text = cols[0].get_text(strip=True)
                driver_name = cols[1].get_text(strip=True)
                points = cols[-1].get_text(strip=True)

                dnf = not pos_text.isdigit()
                position = int(pos_text) if pos_text.isdigit() else None

                results.append(
                    {
                        "position": position,
                        "driver": driver_name,
                        "points": float(points) if points.replace(".", "").isdigit() else 0,
                        "dnf": dnf,
                    }
                )

            logger.info(
                f"Fetched {len(results)} sprint results for {season} R{round_num}"
            )
            return results
        except Exception as e:
            logger.warning(f"Scraper failed for sprint {season}/R{round_num}: {e}")
            return []

    async def fetch_pit_stops(self, season: int, round_num: int) -> list[dict]:
        """Return pit stop data (time, lap, number)."""
        url = f"{self.BASE_URL}/en/results/{season}/races/{round_num}/pit-stop-summary.html"
        try:
            html = await self._fetch_page(url)
            soup = BeautifulSoup(html, "lxml")

            stops: list[dict] = []
            rows = soup.select("table tbody tr")

            for row in rows:
                cols = row.select("td")
                if len(cols) < 4:
                    continue

                stops.append(
                    {
                        "driver": cols[1].get_text(strip=True),
                        "stop_number": int(cols[0].get_text(strip=True))
                        if cols[0].get_text(strip=True).isdigit()
                        else 0,
                        "lap": int(cols[2].get_text(strip=True))
                        if cols[2].get_text(strip=True).isdigit()
                        else 0,
                        "duration": cols[3].get_text(strip=True),
                    }
                )

            logger.info(f"Fetched {len(stops)} pit stops for {season} R{round_num}")
            return stops
        except Exception as e:
            logger.warning(f"Scraper failed for pit stops {season}/R{round_num}: {e}")
            return []

    async def fetch_fastest_laps(self, season: int, round_num: int) -> list[dict]:
        """Return fastest lap data per race."""
        url = f"{self.BASE_URL}/en/results/{season}/races/{round_num}/fastest-laps.html"
        try:
            html = await self._fetch_page(url)
            soup = BeautifulSoup(html, "lxml")

            laps: list[dict] = []
            rows = soup.select("table tbody tr")

            for row in rows:
                cols = row.select("td")
                if len(cols) < 4:
                    continue

                laps.append(
                    {
                        "position": int(cols[0].get_text(strip=True))
                        if cols[0].get_text(strip=True).isdigit()
                        else None,
                        "driver": cols[1].get_text(strip=True),
                        "lap_time": cols[2].get_text(strip=True) if len(cols) > 2 else None,
                        "lap_number": int(cols[3].get_text(strip=True))
                        if len(cols) > 3 and cols[3].get_text(strip=True).isdigit()
                        else None,
                    }
                )

            logger.info(f"Fetched {len(laps)} fastest laps for {season} R{round_num}")
            return laps
        except Exception as e:
            logger.warning(
                f"Scraper failed for fastest laps {season}/R{round_num}: {e}"
            )
            return []

    async def fetch_historical_data(
        self, circuit_name: str, years: int = 5
    ) -> dict:
        """Fetch historical data for a circuit over the last N years."""
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
                if circuit_name.lower() in race.get("race_name", "").lower():
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
            f"Fetched historical data for {circuit_name}: "
            f"{len(data['years_covered'])} years"
        )
        return data

    @staticmethod
    def _parse_lap_time(time_str: str | None) -> float | None:
        """Parse lap time string (e.g., '1:23.456') to seconds."""
        if not time_str or time_str in ("", "DNF", "DNS", "NC"):
            return None
        try:
            parts = time_str.split(":")
            if len(parts) == 2:
                minutes = int(parts[0])
                seconds = float(parts[1])
                return minutes * 60 + seconds
            return float(time_str)
        except (ValueError, IndexError):
            return None

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
