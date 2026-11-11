"""Data sources: the results API, formula1.com and the pool site.

Network is never touched: the API and pool clients run against httpx mock
transports, and the scraper against table markup saved from the live site.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from app.collectors.f1_api_fallback import F1ApiFallback
from app.collectors.f1_scraper import (
    PAGES,
    classify_result_row,
    parse_meetings,
    parse_results_table,
)
from app.collectors.pool_client import (
    PoolClient,
    PoolError,
    PoolNotConfigured,
    fold_name,
    parse_pool_datetime,
)

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Results API (Jolpica)
# --------------------------------------------------------------------------
class TestResultStatus:
    @pytest.mark.parametrize("status", ["Finished", "Lapped", "+1 Lap", "+2 Laps", "+7 Laps"])
    def test_classified_finishers(self, status):
        """`Lapped` is how the API reports a lapped finisher since 2026. A
        parser that only knew `+N Laps` turned 129 finishers of that season
        into retirements."""
        assert F1ApiFallback._is_dnf(status) is False
        assert F1ApiFallback._is_retirement(status) is False

    @pytest.mark.parametrize("status", ["Retired", "Engine", "Accident", "Disqualified", ""])
    def test_retirements(self, status):
        assert F1ApiFallback._is_dnf(status) is True
        assert F1ApiFallback._is_retirement(status) is True

    @pytest.mark.parametrize("status", ["Did not start", "Withdrew", "Did not qualify"])
    def test_non_starters_are_not_retirements(self, status):
        """Not classified, but the pool does not charge its penalty for a DNS."""
        assert F1ApiFallback._is_dnf(status) is True
        assert F1ApiFallback._is_retirement(status) is False


def _result(position, status, code="RUS", ref="russell", team="mercedes", grid=1):
    return {
        "number": "63", "position": str(position), "positionText": str(position),
        "points": "25", "grid": str(grid), "laps": "58", "status": status,
        "Driver": {"driverId": ref, "code": code, "permanentNumber": "63",
                   "givenName": "George", "familyName": "Russell", "nationality": "British"},
        "Constructor": {"constructorId": team, "name": "Mercedes"},
        "FastestLap": {"rank": "1"},
    }


class TestParsing:
    def test_result_row(self):
        row = F1ApiFallback._parse_result(_result(1, "Finished"))
        assert row["position"] == 1 and row["dnf"] is False and row["retired"] is False
        assert row["driver_ref"] == "russell" and row["team_ref"] == "mercedes"
        assert row["driver_number"] == 63 and row["fastest_lap"] is True

    def test_retired_row_has_no_position(self):
        row = F1ApiFallback._parse_result(_result(18, "Retired"))
        assert row["position"] is None and row["dnf"] and row["retired"]
        assert row["dnf_reason"] == "Retired"

    def test_missing_code_falls_back_to_the_family_name(self):
        raw = _result(5, "Finished")
        del raw["Driver"]["code"]
        assert F1ApiFallback._parse_result(raw)["driver_code"] == "RUS"

    def test_calendar_entry(self):
        race = {
            "season": "2026", "round": "2", "raceName": "Chinese Grand Prix",
            "date": "2026-03-15", "time": "07:00:00Z",
            "Sprint": {"date": "2026-03-14", "time": "03:00:00Z"},
            "Qualifying": {"date": "2026-03-14", "time": "07:00:00Z"},
            "Circuit": {"circuitId": "shanghai", "circuitName": "Shanghai International Circuit",
                        "Location": {"lat": "31.3389", "long": "121.22",
                                     "locality": "Shanghai", "country": "China"}},
        }
        cal = F1ApiFallback._parse_calendar_race(race)
        assert cal["start"] == datetime(2026, 3, 15, 7, 0, tzinfo=timezone.utc)
        assert cal["sprint_start"] == datetime(2026, 3, 14, 3, 0, tzinfo=timezone.utc)
        assert cal["is_sprint"] is True and cal["circuit_ref"] == "shanghai"

    def test_sprint_flag_comes_from_the_sprint_object_not_the_name(self):
        race = {"season": "2026", "round": "1", "raceName": "Australian Grand Prix",
                "date": "2026-03-08", "time": "04:00:00Z", "Circuit": {}}
        assert F1ApiFallback._parse_calendar_race(race)["is_sprint"] is False

    def test_unparseable_date_yields_no_start(self):
        assert F1ApiFallback._parse_start("not-a-date", None) is None
        assert F1ApiFallback._parse_start(None, None) is None


def _api(pages: dict[int, dict]):
    """A collector whose HTTP layer serves `pages` keyed by offset."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        offset = int(request.url.params.get("offset", 0))
        return httpx.Response(200, json=pages[offset])

    c = F1ApiFallback()
    c.MIN_REQUEST_INTERVAL = 0.0
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return c, seen


class TestPagination:
    async def test_season_results_follow_every_page_and_merge_split_rounds(self):
        """A round's rows can straddle a page boundary; they must be rejoined."""
        def page(offset, races):
            return {"MRData": {"total": "150", "limit": "100", "offset": str(offset),
                               "RaceTable": {"Races": races}}}

        def race(rnd, n, start=1):
            return {"season": "2026", "round": str(rnd),
                    "Results": [_result(start + i, "Finished", ref=f"d{start + i}") for i in range(n)]}

        collector, seen = _api({
            0: page(0, [race(1, 22), race(2, 12)]),
            100: page(100, [race(2, 10, start=13), race(3, 22)]),
        })
        results = await collector.fetch_season_results(2026)
        await collector.close()

        assert sorted(results) == [1, 2, 3]
        assert [len(results[r]) for r in (1, 2, 3)] == [22, 22, 22]
        assert len(seen) == 2 and "limit=100" in seen[0] and "offset=100" in seen[1]

    async def test_not_found_is_not_retried(self):
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(404)

        c = F1ApiFallback()
        c.MIN_REQUEST_INTERVAL = 0.0
        c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        assert await c.fetch_race_results(2026, 99) == []
        await c.close()
        assert calls == 1


# --------------------------------------------------------------------------
# formula1.com
# --------------------------------------------------------------------------
class TestF1Scraper:
    def test_meetings_are_in_calendar_order_not_key_order(self):
        """Round N is the N-th link on the index, whatever its meeting key."""
        meetings = parse_meetings(fixture("f1_results_index.html"), 2026)
        assert len(meetings) == 23
        assert meetings[0] == {"round": 1, "key": "1279", "slug": "australia"}
        # The replacement race has the highest key but is round 16.
        assert meetings[15] == {"round": 16, "key": "1308", "slug": "bahrain"}
        assert meetings[20]["slug"] == "las-vegas"

    def test_starting_grid(self):
        rows = parse_results_table(fixture("f1_starting_grid.html"))
        assert len(rows) == 22
        assert rows[0]["position"] == 1 and rows[0]["driver_code"] == "RUS"
        assert rows[0]["driver"] == "George Russell" and rows[0]["team"] == "Mercedes"
        assert [r["position"] for r in rows] == list(range(1, 23))

    def test_race_result_classification(self):
        rows = [classify_result_row(r) for r in parse_results_table(fixture("f1_race_result.html"))]
        by_code = {r["driver_code"]: r for r in rows}
        assert by_code["RUS"]["position"] == 1 and by_code["RUS"]["points"] == 25.0
        assert not by_code["RUS"]["dnf"]
        # Retired: not classified, penalised.
        assert by_code["HAD"]["dnf"] and by_code["HAD"]["retired"]
        # Did not start: not classified, NOT penalised.
        assert by_code["PIA"]["dnf"] and not by_code["PIA"]["retired"]
        # Lapped but classified.
        assert by_code["SAI"]["position"] == 15 and not by_code["SAI"]["dnf"]

    def test_qualifying_and_sprint_tables(self):
        quali = parse_results_table(fixture("f1_qualifying.html"))
        assert quali[0]["driver_code"] == "RUS" and quali[0]["q3"] == "1:18.518"
        sprint = [classify_result_row(r) for r in parse_results_table(fixture("f1_sprint_results.html"))]
        assert len(sprint) == 22 and sprint[0]["points"] == 8.0

    def test_page_without_results_is_empty(self):
        """An upcoming session's page has no table yet."""
        assert parse_results_table("<html><body><p>No results available</p></body></html>") == []

    def test_columns_are_read_by_header_not_position(self):
        html = (
            "<table><thead><tr><th>Team</th><th>Driver</th><th>Pos.</th><th>No.</th></tr></thead>"
            "<tbody><tr><td>Ferrari</td><td>Lewis Hamilton HAM</td><td>3</td><td>44</td></tr></tbody></table>"
        )
        (row,) = parse_results_table(html)
        assert (row["position"], row["driver_code"], row["team"], row["number"]) == (3, "HAM", "Ferrari", 44)

    def test_known_pages(self):
        assert PAGES["grid"] == "starting-grid" and PAGES["sprint_grid"] == "sprint-grid"


# --------------------------------------------------------------------------
# Pool site
# --------------------------------------------------------------------------
BASE = "https://pool.test"


def _pool(handler, **kw):
    kw.setdefault("email", "me@example.test")
    kw.setdefault("password", "secret")
    return PoolClient(base_url=BASE, transport=httpx.MockTransport(handler), **kw)


class TestPoolClient:
    async def test_login_sends_the_origin_header_the_site_requires(self):
        seen = {}

        def handler(request):
            if request.url.path == "/api/v1/auth/login":
                seen["origin"] = request.headers.get("origin")
                seen["body"] = json.loads(request.content)
                return httpx.Response(200, json={"nome": "Vitor"},
                                      headers={"set-cookie": "bf1_session=abc; Path=/"})
            seen["cookie"] = request.headers.get("cookie")
            return httpx.Response(200, json={"entries": []})

        client = _pool(handler)
        await client.classification(2026)
        await client.close()
        assert seen["origin"] == BASE
        assert seen["body"] == {"email": "me@example.test", "password": "secret"}
        assert "bf1_session=abc" in seen["cookie"]
        assert client.profile == {"nome": "Vitor"}

    async def test_expired_session_logs_in_again_once(self):
        calls = {"login": 0, "get": 0}

        def handler(request):
            if request.url.path == "/api/v1/auth/login":
                calls["login"] += 1
                return httpx.Response(200, json={})
            calls["get"] += 1
            return httpx.Response(401 if calls["get"] == 1 else 200, json={"ok": True})

        client = _pool(handler)
        assert await client.telemetry(2026) == {"ok": True}
        await client.close()
        assert calls == {"login": 2, "get": 2}

    async def test_only_get_requests_after_login(self):
        """The client is read-only: it must never submit or change a bet."""
        methods = []

        def handler(request):
            methods.append((request.method, request.url.path))
            return httpx.Response(200, json={})

        client = _pool(handler)
        await client.classification(2026)
        await client.classification_history(2026)
        await client.calendar(2026)
        await client.race_bets(2026, race_id=59)
        await client.telemetry(2026)
        await client.my_bets(2026)
        await client.seasons()
        await client.close()
        assert methods[0] == ("POST", "/api/v1/auth/login")
        assert all(m == "GET" for m, _ in methods[1:]) and len(methods) == 8

    async def test_bad_credentials_and_missing_origin_are_explained(self):
        client = _pool(lambda r: httpx.Response(401, json={}))
        with pytest.raises(PoolError, match="rejected"):
            await client.login()
        await client.close()

        client = _pool(lambda r: httpx.Response(403, json={}))
        with pytest.raises(PoolError, match="Origin"):
            await client.login()
        await client.close()

    async def test_unconfigured_client_refuses_cleanly(self):
        client = PoolClient(base_url="", email="", password="")
        assert not client.configured
        with pytest.raises(PoolNotConfigured):
            await client.classification(2026)

    def test_names_are_joined_on_the_folded_family_name(self):
        assert fold_name("Nico Hülkenberg") == fold_name("Nico Hulkenberg") == "hulkenberg"
        assert fold_name("Kimi Antonelli") == fold_name("Andrea Kimi Antonelli")
        assert fold_name("Rafael Câmara") == "camara"
        assert fold_name("") == ""

    def test_pool_timestamps(self):
        got = parse_pool_datetime("2026-11-22T01:00:00-03:00")
        assert got.astimezone(timezone.utc) == datetime(2026, 11, 22, 4, 0, tzinfo=timezone.utc)
        assert parse_pool_datetime("garbage") is None and parse_pool_datetime(None) is None
