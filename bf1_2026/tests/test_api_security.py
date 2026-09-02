"""Tests for the API authentication surface.

Before this was added, every mutating endpoint (POST races, drivers, bets and
the Monte Carlo /simulate route) and every /api/v1/admin/* route was reachable
by anyone, on a port published to all host interfaces. The admin "key" was
SECRET_KEY compared with `!=`, so an unset value let a request through with an
empty header.

These tests exercise the guards through real requests rather than by calling
the dependency directly, so a route that forgets the dependency fails here.
"""

import importlib

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")
TestClient = fastapi_testclient.TestClient

WRITE_KEY = "w" * 40
ADMIN_KEY = "a" * 40

WRITE_ROUTES = [
    ("post", "/api/v1/races/"),
    ("post", "/api/v1/drivers/"),
    ("post", "/api/v1/bets/"),
]
ADMIN_ROUTES = [
    ("get", "/api/v1/admin/dashboard"),
    ("post", "/api/v1/admin/recalculate-elo"),
    ("post", "/api/v1/admin/retrain-model"),
    ("post", "/api/v1/admin/update-data"),
]


def _client(monkeypatch, **env):
    """Rebuild the app under a given environment.

    Settings are read at import time, so the modules have to be reloaded for
    an env change to take effect.
    """
    defaults = {
        "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/db",
        "ENVIRONMENT": "production",
        "WRITE_API_KEY": WRITE_KEY,
        "ADMIN_API_KEY": ADMIN_KEY,
        "ALLOWED_ORIGINS": "https://example.test",
        "ENABLE_DOCS": "false",
    }
    defaults.update(env)
    for k, v in defaults.items():
        monkeypatch.setenv(k, v)

    import app.config
    importlib.reload(app.config)
    import app.utils.security
    importlib.reload(app.utils.security)
    import app.routers.admin, app.routers.bets, app.routers.drivers
    import app.routers.predictions, app.routers.races
    for mod in (app.routers.admin, app.routers.bets, app.routers.drivers,
                app.routers.predictions, app.routers.races):
        importlib.reload(mod)
    import app.main
    importlib.reload(app.main)
    return TestClient(app.main.app, raise_server_exceptions=False)


@pytest.fixture
def client(monkeypatch):
    return _client(monkeypatch)


class TestWriteRoutesRequireAKey:
    @pytest.mark.parametrize("method,path", WRITE_ROUTES)
    def test_rejected_without_key(self, client, method, path):
        assert getattr(client, method)(path, json={}).status_code == 401

    @pytest.mark.parametrize("method,path", WRITE_ROUTES)
    def test_rejected_with_wrong_key(self, client, method, path):
        resp = getattr(client, method)(path, json={}, headers={"X-API-Key": "nope"})
        assert resp.status_code == 401

    def test_simulate_is_guarded(self, client):
        """The heaviest route: unbounded simulations on one CPU."""
        resp = client.post(
            "/api/v1/predictions/race/"
            "00000000-0000-0000-0000-000000000000/simulate",
            json={"n_simulations": 100000},
        )
        assert resp.status_code == 401


class TestAdminRoutesRequireTheAdminKey:
    @pytest.mark.parametrize("method,path", ADMIN_ROUTES)
    def test_rejected_without_key(self, client, method, path):
        assert getattr(client, method)(path).status_code == 401

    @pytest.mark.parametrize("method,path", ADMIN_ROUTES)
    def test_write_key_does_not_open_admin(self, client, method, path):
        """The two keys are separate: a leaked integration token must not
        grant admin operations."""
        resp = getattr(client, method)(path, headers={"X-Admin-Key": WRITE_KEY})
        assert resp.status_code == 401


class TestFailClosed:
    def test_unconfigured_key_refuses_in_production(self, monkeypatch):
        """An unset key must refuse every request, not accept an empty header."""
        client = _client(monkeypatch, WRITE_API_KEY="", ADMIN_API_KEY="")
        assert client.post("/api/v1/races/", json={}).status_code == 503
        assert client.get("/api/v1/admin/dashboard").status_code == 503

    def test_shipped_placeholder_is_not_a_valid_key(self, monkeypatch):
        placeholder = "dev-secret-key-change-in-production"
        client = _client(monkeypatch, ADMIN_API_KEY=placeholder)
        resp = client.get(
            "/api/v1/admin/dashboard", headers={"X-Admin-Key": placeholder}
        )
        assert resp.status_code == 503


class TestPublicSurface:
    def test_health_stays_public(self, client):
        assert client.get("/health").status_code == 200

    def test_docs_are_not_published_in_production(self, client):
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404, path

    def test_docs_can_be_enabled_deliberately(self, monkeypatch):
        client = _client(monkeypatch, ENABLE_DOCS="true")
        assert client.get("/docs").status_code == 200


class TestCors:
    def test_wildcard_origin_disables_credentials(self, monkeypatch):
        """"*" with allow_credentials makes Starlette echo back any origin."""
        _client(monkeypatch, ALLOWED_ORIGINS="*")
        import app.main
        cors = [m for m in app.main.app.user_middleware if "CORS" in str(m)][0]
        assert cors.kwargs["allow_credentials"] is False

    def test_origin_list_is_stripped(self, monkeypatch):
        """"a, b" used to yield " b", which could never match a real Origin."""
        _client(monkeypatch, ALLOWED_ORIGINS="https://a.test, https://b.test")
        import app.main
        cors = [m for m in app.main.app.user_middleware if "CORS" in str(m)][0]
        assert cors.kwargs["allow_origins"] == ["https://a.test", "https://b.test"]
        assert cors.kwargs["allow_credentials"] is True
