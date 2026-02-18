"""Message formatters for Telegram MarkdownV2."""

from __future__ import annotations


def escape_md(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    special = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in str(text))


def format_race_analysis(analysis: dict) -> str:
    """Format a complete race analysis for Telegram."""
    weather = analysis.get("weather", {})
    rain = weather.get("rain_probability", 0)
    risk = weather.get("weather_risk_index", 0)
    temp = weather.get("temperature_air", 25)
    humidity = weather.get("humidity", 50)

    risk_label = "HIGH" if risk > 0.6 else "MED" if risk > 0.3 else "LOW"

    lines = [
        "━━━━━━━━━━━━━━━━━",
        f"🏎 *GP {escape_md(analysis.get('race_name', 'Unknown'))}* — Round {escape_md(str(analysis.get('round', '?')))}",
        f"📅 {escape_md(str(analysis.get('date', 'TBD')))} \\| 🏁 {escape_md(str(analysis.get('circuit', 'TBD')))}",
        "━━━━━━━━━━━━━━━━━",
        "",
        "🌤 *WEATHER*",
        f"  Rain: {rain:.0%} \\| Risk: {escape_md(risk_label)}",
        f"  Temp: {temp:.0f}°C \\| Humidity: {humidity:.0f}%",
        "",
        "📊 *TOP 5 DRIVERS*",
    ]

    for i, d in enumerate(analysis.get("top_drivers", [])[:5], 1):
        lines.append(
            f"  {i}\\. {escape_md(d['code'])} — "
            f"EV: {d['ev']:.1f} \\| "
            f"🏆{d['top3']:.0%} \\| "
            f"⚠️DNF:{d['dnf']:.0%}"
        )

    allocation = analysis.get("allocation", {})
    if allocation:
        lines.append("")
        lines.append("🎯 *STRATEGY*")
        lines.append(f"  Type: {escape_md(analysis.get('strategy_type', 'balanced'))}")
        for code, tokens in allocation.items():
            bar = "●" * tokens + "○" * (5 - tokens)
            lines.append(f"  {escape_md(code)}: \\[{escape_md(bar)}\\] {tokens}T")

    insights = analysis.get("insights", [])
    if insights:
        lines.append("")
        lines.append("💡 *INSIGHTS*")
        for insight in insights:
            lines.append(f"  • {escape_md(insight)}")

    return "\n".join(lines)


def format_bet_confirmation(bet: dict, allocation: dict) -> str:
    """Format a bet confirmation receipt."""
    lines = [
        "━━━━━━━━━━━━━━━━━",
        "✅ *BET CONFIRMED*",
        "━━━━━━━━━━━━━━━━━",
        f"Strategy: {escape_md(bet.get('strategy_type', 'balanced'))}",
        f"EV: {bet.get('expected_value', 0):.2f}",
        "",
    ]

    for code, tokens in allocation.items():
        bar = "●" * tokens + "○" * (5 - tokens)
        lines.append(f"  {escape_md(code)}: \\[{escape_md(bar)}\\] {tokens}T")

    lines.append(f"\nConfirmed at: {escape_md(bet.get('confirmed_at', 'now'))}")
    return "\n".join(lines)


def format_leaderboard(users: list[dict]) -> str:
    """Format the user leaderboard."""
    lines = [
        "━━━━━━━━━━━━━━━━━",
        "🏆 *BF1 LEADERBOARD*",
        "━━━━━━━━━━━━━━━━━",
        "",
    ]

    medals = ["🥇", "🥈", "🥉"]
    for i, u in enumerate(users):
        medal = medals[i] if i < 3 else f"  {i + 1}\\."
        lines.append(
            f"{medal} {escape_md(u.get('name', 'User'))} \\| "
            f"{u.get('points', 0):.1f} pts"
        )

    return "\n".join(lines)


def format_simulation_result(sim_results: dict) -> str:
    """Format Monte Carlo simulation results."""
    lines = [
        "━━━━━━━━━━━━━━━━━",
        "🎲 *SIMULATION RESULTS*",
        "━━━━━━━━━━━━━━━━━",
        "",
    ]

    sorted_drivers = sorted(
        sim_results.items(),
        key=lambda x: x[1].get("avg_position", 20),
    )

    for code, data in sorted_drivers[:10]:
        pos = data.get("avg_position", 20)
        top3 = data.get("top3_probability", 0)
        lines.append(
            f"  {escape_md(code)}: P{pos:.1f} \\| Top3: {top3:.0%}"
        )

    return "\n".join(lines)


def format_alert_message(alert_type: str, data: dict) -> str:
    """Format an alert message."""
    if alert_type == "race_24h":
        return (
            f"⏰ *RACE IN 24 HOURS*\n\n"
            f"🏎 {escape_md(data.get('race_name', 'Race'))}\n"
            f"Don't forget to place your bets\\! Use /bet"
        )
    elif alert_type == "race_1h":
        return (
            f"🚨 *LAST CHANCE \\- 1 HOUR*\n\n"
            f"🏎 {escape_md(data.get('race_name', 'Race'))}\n"
            f"Betting closes soon\\! Use /bet NOW"
        )
    elif alert_type == "weather":
        return (
            f"⚠️ *WEATHER ALERT*\n\n"
            f"Rain probability: {data.get('rain_probability', 0):.0%}\n"
            f"Consider reviewing your strategy\\! Use /analysis"
        )
    elif alert_type == "result":
        return (
            f"🏁 *RACE RESULT*\n\n"
            f"🏎 {escape_md(data.get('race_name', 'Race'))}\n"
            f"Your points: {data.get('points', 0):.1f}\n"
            f"Check /leaderboard for standings"
        )
    return escape_md(str(data))


def ascii_bar_chart(data: dict[str, float], width: int = 30) -> str:
    """Generate a simple ASCII bar chart."""
    if not data:
        return ""
    max_val = max(data.values()) if data.values() else 1
    lines = []
    for label, value in data.items():
        bar_len = int((value / max_val) * width) if max_val > 0 else 0
        bar = "█" * bar_len + "░" * (width - bar_len)
        lines.append(f"{label:>5}: {bar} {value:.1f}")
    return "\n".join(lines)
