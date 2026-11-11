"""Read-only client for the BF1 pool site.

The pool ("Bolão Fórmula 1") is a Next.js front end over a JSON API. Nothing
needs to be scraped out of HTML: every page of the site is a thin view over
one of the endpoints below, so this client calls them directly — which is both
exact and far less brittle than parsing rendered markup.

Authentication is a session cookie obtained by POSTing e-mail and password to
``/api/v1/auth/login``. The API rejects that POST with 403 unless it carries an
``Origin`` header matching the site, which is the one non-obvious detail.

This client only ever *reads*. It never submits or changes a bet: placing a
bet is the participant's own act, on the site.

Configuration (all three are required, in ``.env``):

    POOL_BASE_URL=https://<the pool's address>
    POOL_EMAIL=...
    POOL_PASSWORD=...
"""

from __future__ import annotations

import unicodedata
from datetime import datetime

import httpx
from loguru import logger

from app.config import settings


class PoolError(RuntimeError):
    """The pool API could not be read."""


class PoolNotConfigured(PoolError):
    """POOL_BASE_URL / POOL_EMAIL / POOL_PASSWORD are not all set."""


def fold_name(name: str) -> str:
    """Accent- and case-insensitive family name: 'Nico Hülkenberg' -> 'hulkenberg'.

    The pool, the results API and formula1.com spell drivers differently
    ("Kimi Antonelli" / "Andrea Kimi Antonelli", "Rafael Câmara" / "Camara").
    Within one grid the family name is unique, so it is the join key.
    """
    ascii_name = (
        unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    )
    parts = ascii_name.lower().replace(".", " ").split()
    return parts[-1] if parts else ""


class PoolClient:
    """Authenticated, read-only access to the pool's API."""

    def __init__(
        self,
        base_url: str | None = None,
        email: str | None = None,
        password: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url if base_url is not None else settings.pool_base_url).rstrip("/")
        self._email = email if email is not None else settings.pool_email
        self._password = password if password is not None else settings.pool_password
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._logged_in = False
        self.profile: dict = {}

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self._email and self._password)

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": "Mozilla/5.0 (compatible; bf1-quant/1.0)",
            "Accept": "application/json",
            # Required: the API answers 403 to a login without a matching Origin.
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/",
        }

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(30.0),
                headers=self._headers(),
                follow_redirects=True,
                transport=self._transport,
            )
            self._logged_in = False
        return self._client

    async def login(self) -> dict:
        """Open a session. Returns the account profile (name, role, timezone)."""
        if not self.configured:
            raise PoolNotConfigured(
                "Pool access is not configured: set POOL_BASE_URL, POOL_EMAIL and "
                "POOL_PASSWORD."
            )
        client = await self._get_client()
        try:
            resp = await client.post(
                "/api/v1/auth/login",
                json={"email": self._email, "password": self._password},
            )
        except httpx.HTTPError as e:
            raise PoolError(f"Could not reach the pool site: {e}") from e
        if resp.status_code in (401, 422):
            raise PoolError("The pool rejected the e-mail/password.")
        if resp.status_code == 403:
            raise PoolError(
                "The pool refused the login request (403). It checks the Origin "
                "header — POOL_BASE_URL must be the site's exact address."
            )
        if resp.status_code >= 400:
            raise PoolError(f"Pool login failed with HTTP {resp.status_code}.")
        self._logged_in = True
        profile = resp.json()
        self.profile = profile if isinstance(profile, dict) else {}
        logger.info(f"Logged in to the pool as {profile.get('nome', '?')}")
        return profile

    async def _get(self, path: str, params: dict | None = None):
        client = await self._get_client()
        if not self._logged_in:
            await self.login()
        try:
            resp = await client.get(path, params=params)
            if resp.status_code == 401:          # session expired: log in once more
                await self.login()
                resp = await client.get(path, params=params)
        except httpx.HTTPError as e:
            raise PoolError(f"Could not reach the pool site: {e}") from e
        if resp.status_code >= 400:
            raise PoolError(f"Pool API {path} answered HTTP {resp.status_code}.")
        try:
            return resp.json()
        except ValueError as e:
            raise PoolError(f"Pool API {path} did not return JSON.") from e

    # ---- endpoints (all GET) ------------------------------------------ #
    async def seasons(self) -> list[str]:
        return await self._get("/api/v1/calendar/seasons")

    async def classification(self, season: int | str) -> dict:
        """Standings: entries[] with total, discard, valid_total, eleventh_hits."""
        return await self._get("/api/v1/classification", {"season": str(season)})

    async def classification_history(self, season: int | str) -> dict:
        """Every participant's score in every scored event."""
        return await self._get("/api/v1/classification/history", {"season": str(season)})

    async def calendar(self, season: int | str) -> list[dict]:
        """Events (race and sprint are separate), with the pool's own times."""
        return await self._get("/api/v1/calendar", {"season": str(season)})

    async def race_bets(self, season: int | str, race_id: int | None = None) -> dict:
        """The betting form's data: open event, deadline, drivers, rules, my bet."""
        params = {"season": str(season)}
        if race_id is not None:
            params["race_id"] = str(race_id)
        return await self._get("/api/v1/race-bets", params)

    async def telemetry(self, season: int | str) -> dict:
        """My dashboard: next event, current rules, my position, weather."""
        return await self._get("/api/v1/telemetry", {"season": str(season)})

    async def my_bets(self, season: int | str) -> dict:
        """Every bet I placed, with what each pick scored."""
        return await self._get("/api/v1/telemetry/bets", {"season": str(season)})

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._logged_in = False


def parse_pool_datetime(value: str | None) -> datetime | None:
    """Parse the pool's ISO timestamps ('2026-11-22T01:00:00-03:00')."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
