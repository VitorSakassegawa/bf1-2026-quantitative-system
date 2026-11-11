"""formula1.com results scraper.

What this is for
----------------
The results API (Jolpica) only reports a race's starting grid *after* the
race, as a column of the result. Before the start — which is when a bet has to
be placed — the only public source of the official grid, with penalties
applied, is formula1.com's "starting grid" page. Since the grid is by far the
most informative input the model has, that page is the reason this scraper
exists. It also serves as a second source for results when the API is late.

What changed on the site
------------------------
The old URLs (``/en/results/{season}/races/{round}/race-result.html``) all
return 404. Pages now live at

    /en/results/{season}/races/{meeting_key}/{slug}/{page}

where ``meeting_key`` is an opaque number (1279 for Australia 2026) that has
nothing to do with the round. The season index page lists the meetings in
calendar order, so the round-th link on it is the round's meeting.

Parsing is done by column *header*, not column position, and the driver cell
("George Russell RUS") is split on its trailing three-letter code, so a column
being added or reordered does not silently shift every value.
"""

from __future__ import annotations

import asyncio
import re
import time

import httpx
from bs4 import BeautifulSoup
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings

_MEETING_LINK = re.compile(r"/en/results/(\d{4})/races/(\d+)/([a-z0-9-]+)/")
_DRIVER_CELL = re.compile(r"^(?P<name>.+?)\s*(?P<code>[A-Z]{3})$")

# Session name -> page slug on formula1.com
PAGES = {
    "race": "race-result",
    "grid": "starting-grid",
    "qualifying": "qualifying",
    "sprint": "sprint-results",
    "sprint_grid": "sprint-grid",
    "sprint_qualifying": "sprint-qualifying",
    "pit_stops": "pit-stop-summary",
    "fastest_laps": "fastest-laps",
}

_NOT_STARTED = {"DNS", "DNQ", "WD"}


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


def _key(header: str) -> str:
    """'Time / Retired' -> 'time_retired', 'Pos.' -> 'pos'."""
    return re.sub(r"[^a-z0-9]+", "_", header.lower()).strip("_")


def parse_results_table(html: str) -> list[dict]:
    """Parse the first results table of a formula1.com page into row dicts.

    Every row carries the raw cells keyed by normalised header, plus:
    ``position`` (int or None), ``position_text``, ``number``, ``driver``,
    ``driver_code`` and ``team``. Returns [] when the page has no table yet
    (a session that has not happened).
    """
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if table is None:
        return []
    headers = [_key(th.get_text(" ", strip=True)) for th in table.find_all("th")]
    if not headers:
        return []

    rows: list[dict] = []
    for tr in table.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if not cells or len(cells) != len(headers):
            continue
        raw = dict(zip(headers, cells))
        pos_text = raw.get("pos", "")
        driver_cell = re.sub(r"\s+", " ", raw.get("driver", "")).strip()
        m = _DRIVER_CELL.match(driver_cell)
        number = raw.get("no", "")
        rows.append(
            {
                **raw,
                "position": int(pos_text) if pos_text.isdigit() else None,
                "position_text": pos_text,
                "number": int(number) if number.isdigit() else None,
                "driver": (m.group("name") if m else driver_cell).strip(),
                "driver_code": m.group("code") if m else "",
                "team": raw.get("team", ""),
            }
        )
    return rows


def classify_result_row(row: dict) -> dict:
    """Add ``dnf`` / ``retired`` / ``status`` to a race or sprint result row.

    On the site a non-classified car has ``Pos.`` = NC (or DQ/EX) and the
    time column says why: DNF, DNS, DSQ — or a lap deficit for a car that was
    running but too far back to be classified.
    """
    status = (row.get("time_retired") or "").strip()
    upper = status.upper()
    classified = row.get("position") is not None
    not_started = upper in _NOT_STARTED
    return {
        **row,
        "status": status,
        "dnf": not classified,
        # The pool charges its retirement penalty for a car that started and
        # did not finish; a DNS is not a retirement.
        "retired": (not classified) and not not_started,
        "points": float(row["pts"]) if str(row.get("pts", "")).replace(".", "", 1).isdigit() else 0.0,
        "laps": int(row["laps"]) if str(row.get("laps", "")).isdigit() else 0,
    }


def parse_meetings(html: str, season: int) -> list[dict]:
    """Meetings of a season in calendar order: [{round, key, slug}]."""
    seen: list[tuple[str, str]] = []
    for year, key, slug in _MEETING_LINK.findall(html):
        if int(year) == season and (key, slug) not in seen:
            seen.append((key, slug))
    return [
        {"round": i, "key": key, "slug": slug} for i, (key, slug) in enumerate(seen, start=1)
    ]


class F1DataCollector:
    """Scraper for formula1.com session results."""

    BASE_URL = "https://www.formula1.com"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._last_request_at = 0.0
        self._meetings: dict[int, list[dict]] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(40.0),
                follow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; bf1-quant/1.0)",
                    "Accept-Language": "en",
                },
            )
        return self._client

    async def _rate_limit(self) -> None:
        delay = settings.scraping_delay_seconds
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        self._last_request_at = time.monotonic()

    @retry(
        retry=retry_if_exception(_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=20),
        reraise=True,
    )
    async def _fetch_page(self, url: str) -> str:
        await self._rate_limit()
        client = await self._get_client()
        logger.debug(f"Fetching {url}")
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text

    # ------------------------------------------------------------------ #
    async def fetch_meetings(self, season: int) -> list[dict]:
        """The season's meetings in calendar order (cached per instance)."""
        if season not in self._meetings:
            html = await self._fetch_page(f"{self.BASE_URL}/en/results/{season}/races")
            meetings = parse_meetings(html, season)
            if not meetings:
                logger.warning(
                    f"No meeting links found on the {season} results index — "
                    "the page structure may have changed again."
                )
            self._meetings[season] = meetings
        return self._meetings[season]

    async def fetch_session(self, season: int, round_num: int, session: str) -> list[dict]:
        """Rows of one session page; [] if the session has no results yet."""
        page = PAGES.get(session)
        if page is None:
            raise ValueError(f"Unknown session {session!r}; expected one of {sorted(PAGES)}")
        try:
            meetings = await self.fetch_meetings(season)
            meeting = next((m for m in meetings if m["round"] == round_num), None)
            if meeting is None:
                logger.warning(f"{season} R{round_num}: no such meeting on formula1.com")
                return []
            url = (
                f"{self.BASE_URL}/en/results/{season}/races/"
                f"{meeting['key']}/{meeting['slug']}/{page}"
            )
            rows = parse_results_table(await self._fetch_page(url))
            logger.info(f"formula1.com {season} R{round_num} {session}: {len(rows)} rows")
            return rows
        except Exception as e:
            logger.warning(f"formula1.com {session} failed for {season}/R{round_num}: {e}")
            return []

    # ------------------------------------------------------------------ #
    async def fetch_starting_grid(
        self, season: int, round_num: int, sprint: bool = False
    ) -> list[dict]:
        """The official starting grid, penalties applied. [] until published."""
        rows = await self.fetch_session(
            season, round_num, "sprint_grid" if sprint else "grid"
        )
        return [r for r in rows if r["position"] is not None and r["driver_code"]]

    async def fetch_qualifying_results(
        self, season: int, round_num: int, sprint: bool = False
    ) -> list[dict]:
        return await self.fetch_session(
            season, round_num, "sprint_qualifying" if sprint else "qualifying"
        )

    async def fetch_race_results(self, season: int, round_num: int) -> list[dict]:
        rows = await self.fetch_session(season, round_num, "race")
        return [classify_result_row(r) for r in rows]

    async def fetch_sprint_results(self, season: int, round_num: int) -> list[dict]:
        rows = await self.fetch_session(season, round_num, "sprint")
        return [classify_result_row(r) for r in rows]

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
