"""Message formatters for Telegram MarkdownV2.

Tables are rendered inside code spans: within a code span MarkdownV2 only
needs the backtick and backslash escaped, which avoids the whole class of
"unescaped dot -> 400 can't parse entities" failures that used to silently
kill /analysis, /bet and /leaderboard.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.config import settings

_SOURCE_LABEL = {
    "official": "grid oficial",
    "qualifying": "ordem da classificação (penalidades de grid ainda não aplicadas)",
    "provisional": "provisório — ainda sem classificação",
}


def escape_md(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    special = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in str(text))


def code(text: str) -> str:
    """Inline code span (monospace); only ` and \\ need escaping inside."""
    return "`" + str(text).replace("\\", "\\\\").replace("`", "'") + "`"


def local_time(value: datetime | str | None, fmt: str = "%d/%m %H:%M") -> str:
    """Render a moment in the configured display time zone."""
    if value is None:
        return "?"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    try:
        zone = ZoneInfo(settings.display_timezone)
    except Exception:
        zone = timezone.utc
    return value.astimezone(zone).strftime(fmt)


def _countdown(deadline: datetime | str | None) -> str:
    if deadline is None:
        return ""
    if isinstance(deadline, str):
        try:
            deadline = datetime.fromisoformat(deadline)
        except ValueError:
            return ""
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    seconds = (deadline - datetime.now(timezone.utc)).total_seconds()
    if seconds <= 0:
        return "encerrado"
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    if days:
        return f"faltam {days}d {hours}h"
    return f"faltam {hours}h {rest // 60:02d}min"


def format_strategy(strategy: dict, pool: dict | None = None) -> str:
    """The recommended bet for a session: chips, 11th-place pick, what to expect."""
    race = strategy.get("race", {})
    deadline = (pool or {}).get("deadline") or race.get("deadline")
    grid_source = strategy.get("grid_source", "provisional")
    rng = strategy.get("score_range", {})

    lines = [
        f"🏎 *{escape_md(race.get('name', 'Corrida'))}* — R{escape_md(race.get('round', '?'))}",
        f"🏁 {escape_md(race.get('circuit', ''))}",
        f"⏰ Aposta até {escape_md(local_time(deadline))} \\({escape_md(_countdown(deadline))}\\)",
        f"📋 Grid: {escape_md(_SOURCE_LABEL.get(grid_source, grid_source))}",
        "",
        "🎯 *APOSTA RECOMENDADA*",
    ]
    for d in strategy.get("driver_details", []):
        bar = "●" * d["tokens"] + "○" * (5 - d["tokens"])
        grid = f"P{d['grid_position']}" if d.get("grid_position") else "–"
        lines.append(
            code(
                f"{d['driver_code']:<3} {bar} {d['tokens']}  {grid:<3} "
                f"pódio {d['top3_probability']:>4.0%}  DNF {d['dnf_probability']:>3.0%}"
            )
        )

    candidates = strategy.get("eleventh_candidates", [])
    if candidates:
        first = candidates[0]
        others = ", ".join(
            f"{c['driver_code']} {c['probability']:.0%}" for c in candidates[1:]
        )
        lines.append("")
        chance = escape_md(f"{first['probability']:.0%}")
        lines.append(
            f"🔢 *11º lugar:* {escape_md(first['driver_code'])} \\({chance}\\)"
        )
        if others:
            lines.append(escape_md(f"   alternativas: {others}"))
    history = strategy.get("eleventh_history", [])
    if history:
        record = ", ".join(f"{h['driver_code']} {h['times']}×" for h in history)
        lines.append(escape_md(f"   quem mais chegou em 11º no ano: {record}"))

    lines.append("")
    expected = f"{strategy.get('total_ev', 0):.0f} de {strategy.get('max_score', 0):.0f}"
    lines.append(f"📊 Esperado: *{escape_md(expected)}* pontos")
    if rng.get("p10") is not None:
        lines.append(
            escape_md(f"   faixa provável das fichas: {rng['p10']:.0f} a {rng['p90']:.0f}")
        )

    if strategy.get("constraint_errors"):
        lines.append("")
        lines.append("⚠️ " + escape_md("; ".join(strategy["constraint_errors"])))

    if pool:
        lines.append("")
        bet = pool.get("current_bet")
        if bet:
            mine = ", ".join(
                f"{a.get('driver', '?').split()[-1]} {a.get('chips', '?')}"
                for a in bet.get("allocations", [])
            )
            lines.append("🗳 *Sua aposta no site:* " + escape_md(mine))
            if bet.get("eleventh_driver"):
                lines.append(escape_md(f"   11º: {bet['eleventh_driver']}"))
        elif pool.get("is_open"):
            lines.append("🗳 Você ainda *não apostou* nesta prova no site\\.")

    insights = strategy.get("insights", [])
    if insights:
        lines.append("")
        lines.extend(f"• {escape_md(i)}" for i in insights)
    return "\n".join(lines)


def format_field(strategy: dict, limit: int = 22) -> str:
    """The simulated finishing order for the whole field."""
    race = strategy.get("race", {})
    lines = [
        f"🎲 *{escape_md(race.get('name', 'Corrida'))}* — simulação",
        escape_md(f"{strategy.get('n_simulations', 0):,} corridas simuladas".replace(",", ".")),
        "",
        code("    pil  grid  vit  pódio  top10  11º  DNF"),
    ]
    for i, d in enumerate(strategy.get("field", [])[:limit], 1):
        grid = f"P{d['grid_position']}" if d.get("grid_position") else "–"
        lines.append(
            code(
                f"{i:>2}  {d['driver_code']:<3}  {grid:<4} {d['win_probability']:>4.0%} "
                f"{d['top3_probability']:>5.0%} {d['top10_probability']:>6.0%} "
                f"{d['eleventh_probability']:>4.0%} {d['dnf_probability']:>4.0%}"
            )
        )
    pit = strategy.get("pit_stop_projection") or {}
    if pit.get("label"):
        lines.append("")
        lines.append(f"🛞 Estratégia provável: *{escape_md(pit['label'])}*")
    return "\n".join(lines)


def format_classification(standings: dict) -> str:
    """The pool's classification, with the participant's row and gaps."""
    entries = standings.get("entries", [])
    me = standings.get("me")
    lines = [
        f"🏆 *Classificação BF1 {escape_md(standings.get('season', ''))}*",
        escape_md("pontos válidos (com descarte provisório)")
        if standings.get("discard_active")
        else escape_md("pontos totais"),
        "",
    ]
    for e in entries:
        mark = "▶" if me is not None and e is me else " "
        name = str(e.get("participant", "?"))
        short = name if len(name) <= 18 else name[:17] + "…"
        lines.append(
            code(
                f"{mark}{e.get('position', '?'):>2} {short:<18} "
                f"{e.get('valid_total', e.get('total', 0)):>6.0f}  11º×{e.get('eleventh_hits', 0)}"
            )
        )

    if me is not None and entries:
        mine = me.get("valid_total", me.get("total", 0))
        lines.append("")
        points = escape_md(f"{mine:.0f}")
        lines.append(
            f"📍 Você está em *{escape_md(me.get('position', '?'))}º* com {points} pontos"
        )
        ahead = [e for e in entries if e.get("position", 0) < me.get("position", 0)]
        if ahead:
            nxt = ahead[-1]
            gap = nxt.get("valid_total", 0) - mine
            lines.append(
                escape_md(f"   {gap:.0f} atrás do {nxt.get('position')}º ({nxt.get('participant')})")
            )
            podium = next((e for e in entries if e.get("position") == 3), None)
            if podium is not None and me.get("position", 0) > 3:
                lines.append(
                    escape_md(f"   {podium.get('valid_total', 0) - mine:.0f} atrás do pódio (3º)")
                )
        behind = [e for e in entries if e.get("position", 0) > me.get("position", 0)]
        if behind:
            lines.append(
                escape_md(f"   {mine - behind[0].get('valid_total', 0):.0f} à frente do {behind[0].get('position')}º")
            )
    return "\n".join(lines)


def format_local_leaderboard(users: list) -> str:
    """Scores of the bets recorded through this bot (not the pool's table)."""
    lines = ["🏆 *Apostas registradas neste bot*", ""]
    if not users:
        lines.append("_Nenhuma aposta registrada ainda\\._")
        return "\n".join(lines)
    for i, u in enumerate(users, 1):
        name = u.username or f"User {u.telegram_id}"
        lines.append(code(f"{i:>2} {name[:18]:<18} {u.total_points:>7.1f}  ({u.total_bets} apostas)"))
    return "\n".join(lines)


def format_alert_message(alert_type: str, data: dict) -> str:
    """Format an alert message."""
    name = escape_md(data.get("race_name", "Corrida"))
    if alert_type == "race_24h":
        return (
            f"⏰ *FALTAM 24 HORAS*\n\n🏎 {name}\n"
            "Veja a aposta recomendada com /analysis"
        )
    if alert_type == "race_1h":
        return (
            f"🚨 *ÚLTIMA HORA*\n\n🏎 {name}\n"
            "As apostas fecham na largada\\. /analysis"
        )
    if alert_type == "weather":
        rain = escape_md(f"{data.get('rain_probability', 0):.0%}")
        return f"⚠️ *ALERTA DE CLIMA*\n\nChance de chuva: {rain}\nReveja com /analysis"
    if alert_type == "result":
        points = escape_md(f"{data.get('points', 0):.1f}")
        return f"🏁 *RESULTADO*\n\n🏎 {name}\nSua pontuação: {points}\n/classificacao"
    return escape_md(str(data))
