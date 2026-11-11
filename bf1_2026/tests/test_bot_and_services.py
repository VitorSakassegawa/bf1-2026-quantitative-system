"""Bot messages and the service helpers behind them."""

import re
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.optimizer.strategy_engine import StrategyEngine
from app.rules.pool_rules import DEFAULT_RULES as R
from app.services.grid_service import _match_team
from app.services.pool_service import find_me, match_pool_drivers
from app.services.settlement_service import result_lines
from app.telegram.formatters import (
    code,
    escape_md,
    format_alert_message,
    format_classification,
    format_field,
    format_local_leaderboard,
    format_strategy,
    local_time,
)
from app.utils.validators import bet_deadline_for, is_bet_deadline_passed

TEAMS = ["Mercedes", "Ferrari", "McLaren", "Red Bull", "Aston Martin", "Alpine",
         "Williams", "Racing Bulls", "Haas", "Audi", "Cadillac"]
_MD_SPECIAL = set("[]()~>#+-=|{}.!")


def markdown_problems(text: str) -> list[str]:
    """What Telegram's MarkdownV2 parser would reject.

    Outside code spans every special character must be backslash-escaped, and
    the emphasis markers must pair up. One stray `.` makes Telegram answer 400
    and the whole message is lost — which silently broke three commands.
    """
    stripped = re.sub(r"`[^`]*`", "", text)
    stripped = re.sub(r"\\.", "", stripped)
    problems = [f"unescaped {c!r}" for c in sorted(set(stripped) & _MD_SPECIAL)]
    if stripped.count("*") % 2:
        problems.append("unbalanced *")
    if stripped.count("_") % 2:
        problems.append("unbalanced _")
    if text.count("`") % 2:
        problems.append("unbalanced `")
    return problems


@pytest.fixture(scope="module")
def strategy():
    drivers = [
        {"driver_id": f"d{i}", "team_id": TEAMS[i // 2], "team_name": TEAMS[i // 2],
         "code": f"D{i:02d}", "name": f"Driver {i}", "grid_position": i + 1, "elo": 1500}
        for i in range(22)
    ]
    s = StrategyEngine().generate_strategy(
        drivers, {}, {"rain_probability": 0.6, "weather_risk_index": 0.5},
        {d["driver_id"]: d["team_id"] for d in drivers}, n_simulations=2000,
    )
    start = datetime.now(timezone.utc) + timedelta(days=2, hours=3)
    s["race"] = {"name": "Las Vegas Grand Prix", "round": 21, "circuit": "Las Vegas Strip (Street) Circuit",
                 "deadline": start.isoformat(), "date": start.isoformat()}
    s["grid_source"] = "official"
    return s


STANDINGS = {
    "season": 2026,
    "discard_active": True,
    "entries": [
        {"position": 1, "participant": "Ana_Líder (A.)", "valid_total": 5493.0, "eleventh_hits": 8},
        {"position": 2, "participant": "Bruno", "valid_total": 5481.5, "eleventh_hits": 8},
        {"position": 3, "participant": "Carla de Souza e Silva Longo Nome", "valid_total": 5425.0, "eleventh_hits": 9},
        {"position": 4, "participant": "Vitor S.", "valid_total": 5089.2, "eleventh_hits": 3},
        {"position": 5, "participant": "Dani", "valid_total": 4992.0, "eleventh_hits": 2},
    ],
}


class TestMessagesAreValidMarkdown:
    def test_strategy(self, strategy):
        text = format_strategy(strategy)
        assert markdown_problems(text) == []
        assert "APOSTA RECOMENDADA" in text and "11º lugar" in text
        with_history = dict(strategy, eleventh_history=[{"driver_code": "HUL", "times": 5},
                                                        {"driver_code": "BOR", "times": 5}])
        text2 = format_strategy(with_history)
        assert markdown_problems(text2) == [] and "HUL 5×, BOR 5×" in text2
        assert "D00" in text and "grid oficial" in text

    def test_strategy_with_pool_context(self, strategy):
        pool = {
            "deadline": datetime.now(timezone.utc) + timedelta(hours=5),
            "is_open": True,
            "current_bet": {
                "allocations": [{"driver": "Max Verstappen", "chips": 5},
                                {"driver": "Nico Hülkenberg", "chips": 1}],
                "eleventh_driver": "Pierre Gasly (ALP)",
            },
        }
        text = format_strategy(strategy, pool)
        assert markdown_problems(text) == []
        assert "Sua aposta no site" in text and "Verstappen 5" in text

        pool["current_bet"] = None
        text = format_strategy(strategy, pool)
        assert markdown_problems(text) == [] and "não apostou" in text

    def test_strategy_with_errors_and_no_picks(self):
        text = format_strategy({"race": {"name": "X (test) - 1.0"}, "constraint_errors": ["a.b - c!"]})
        assert markdown_problems(text) == []

    def test_field(self, strategy):
        text = format_field(strategy)
        assert markdown_problems(text) == []
        assert text.count("\n") > 22

    def test_classification(self):
        standings = dict(STANDINGS, me=STANDINGS["entries"][3])
        text = format_classification(standings)
        assert markdown_problems(text) == []
        assert "Você está em *4º*" in text
        assert "336 atrás do pódio" in text and "97 à frente do 5º" in text
        assert "▶" in text

    def test_classification_without_my_row(self):
        text = format_classification(dict(STANDINGS, me=None))
        assert markdown_problems(text) == [] and "Você está" not in text

    def test_local_leaderboard(self):
        users = [SimpleNamespace(username="vitor_s.", telegram_id=1, total_points=291.5, total_bets=3),
                 SimpleNamespace(username=None, telegram_id=42, total_points=0.0, total_bets=1)]
        assert markdown_problems(format_local_leaderboard(users)) == []
        assert markdown_problems(format_local_leaderboard([])) == []

    @pytest.mark.parametrize("kind", ["race_24h", "race_1h", "weather", "result", "other"])
    def test_alerts(self, kind):
        data = {"race_name": "São Paulo G.P. (Interlagos)", "rain_probability": 0.35, "points": 212.5}
        assert markdown_problems(format_alert_message(kind, data)) == []

    def test_helpers(self):
        assert escape_md("a.b-c!") == r"a\.b\-c\!"
        assert code("a`b\\c") == "`a'b\\\\c`"
        assert local_time(datetime(2026, 11, 22, 4, 0, tzinfo=timezone.utc)) == "22/11 01:00"
        assert local_time(None) == "?"


class TestDeadline:
    def test_bets_close_at_the_start_not_an_hour_before(self):
        """The pool: 'até o horário oficial da prova'."""
        start = datetime(2026, 11, 22, 4, 0, tzinfo=timezone.utc)
        assert bet_deadline_for(start) == start

    def test_stored_pool_deadline_wins(self):
        """The pool's own time for Qatar 2026 is three hours before the start."""
        start = datetime(2026, 11, 29, 16, 0, tzinfo=timezone.utc)
        pool = datetime(2026, 11, 29, 13, 0, tzinfo=timezone.utc)
        assert bet_deadline_for(start, pool) == pool

    def test_passed_and_naive_datetimes(self):
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        assert is_bet_deadline_passed(past) and not is_bet_deadline_passed(future)
        assert is_bet_deadline_passed(past.replace(tzinfo=None))


class TestSettlement:
    def _row(self, position, dnf=False, reason=None):
        return SimpleNamespace(driver_id=uuid.uuid4(), final_position=position, dnf=dnf, dnf_reason=reason)

    def test_result_lines_distinguish_retired_from_not_started(self):
        win, retired, dns = self._row(1), self._row(None, True, "Retired"), self._row(None, True, "Did not start")
        lines = result_lines([win, retired, dns])
        assert lines[str(win.driver_id)].position == 1
        assert lines[str(retired.driver_id)].retired is True
        assert lines[str(dns.driver_id)].retired is False and lines[str(dns.driver_id)].position is None

    def test_a_stored_bet_scores_like_the_pool(self):
        rows = [self._row(p) for p in range(1, 12)] + [self._row(None, True, "Retired")]
        lines = result_lines(rows)
        ids = [str(r.driver_id) for r in rows]
        alloc = {ids[0]: 5, ids[1]: 5, ids[2]: 3, ids[3]: 1, ids[11]: 1}
        got = R.score(alloc, ids[10], lines)
        assert got.total == 5 * 25 + 5 * 18 + 3 * 15 + 12 - 10 + 50
        assert got.eleventh_hit


class TestMatching:
    def test_my_row_is_found_by_name_then_family_name(self):
        entries = [{"participant": "Maria Tanaka"}, {"participant": "João Pereira"}]
        assert find_me(entries, "maria tanaka") is entries[0]
        assert find_me(entries, "M. Tanaka") is entries[0]
        assert find_me(entries, "Someone Else") is None
        assert find_me(entries, "") is None

    def test_ambiguous_family_name_is_not_guessed(self):
        entries = [{"participant": "Ana Silva"}, {"participant": "Beto Silva"}]
        assert find_me(entries, "C. Silva") is None

    def test_pool_drivers_map_onto_local_ones(self):
        local = [SimpleNamespace(id="1", name="Andrea Kimi Antonelli"),
                 SimpleNamespace(id="2", name="Nico Hulkenberg"),
                 SimpleNamespace(id="3", name="Rafael Camara")]
        pool = [{"name": "Kimi Antonelli"}, {"name": "Nico Hülkenberg"},
                {"name": "Rafael Câmara"}, {"name": "Unknown Driver"}]
        assert match_pool_drivers(pool, local) == {"antonelli": "1", "hulkenberg": "2", "camara": "3"}

    def test_site_team_names_map_onto_local_teams(self):
        teams = [SimpleNamespace(name=n, full_name=f) for n, f in [
            ("Red Bull", "Oracle Red Bull Racing"), ("Racing Bulls", "Visa Cash App Racing Bulls F1 Team"),
            ("Haas", "MoneyGram Haas F1 Team"), ("Mercedes", "Mercedes-AMG Petronas F1 Team")]]
        assert _match_team("Red Bull Racing", teams).name == "Red Bull"
        assert _match_team("Racing Bulls", teams).name == "Racing Bulls"
        assert _match_team("Haas F1 Team", teams).name == "Haas"
        assert _match_team("Mercedes", teams).name == "Mercedes"
        assert _match_team("Lotus", teams) is None
