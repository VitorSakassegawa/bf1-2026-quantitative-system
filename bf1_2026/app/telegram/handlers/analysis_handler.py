"""Analysis handler – show detailed race analysis."""

from __future__ import annotations

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from app.telegram.formatters import format_race_analysis


async def analysis_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /analysis [race_id] – show complete race analysis."""
    if update.effective_message is None:
        return

    logger.info(f"/analysis from {update.effective_user}")

    # Parse optional race argument
    args = context.args or []
    race_id = args[0] if args else None

    # Placeholder analysis data (in production, fetch from DB + strategy engine)
    analysis = {
        "race_name": "Next Grand Prix",
        "round": "?",
        "circuit": "TBD",
        "date": "TBD",
        "weather": {
            "rain_probability": 0.15,
            "weather_risk_index": 0.12,
            "temperature_air": 28.0,
            "humidity": 55.0,
        },
        "top_drivers": [
            {"code": "VER", "ev": 12.5, "top3": 0.45, "dnf": 0.03},
            {"code": "NOR", "ev": 10.2, "top3": 0.38, "dnf": 0.04},
            {"code": "LEC", "ev": 9.8, "top3": 0.35, "dnf": 0.05},
            {"code": "HAM", "ev": 8.1, "top3": 0.28, "dnf": 0.04},
            {"code": "PIA", "ev": 7.5, "top3": 0.22, "dnf": 0.05},
        ],
        "strategy_type": "balanced",
        "allocation": {
            "VER": 4, "NOR": 3, "LEC": 3, "HAM": 3, "PIA": 2,
        },
        "insights": [
            "Low rain probability – dry setup favored",
            "High overtaking index – grid less important",
        ],
    }

    text = format_race_analysis(analysis)
    await update.effective_message.reply_text(text, parse_mode="MarkdownV2")
