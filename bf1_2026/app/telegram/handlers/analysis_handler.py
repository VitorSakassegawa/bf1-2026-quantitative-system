"""Analysis handler – real data-driven race analysis."""

from __future__ import annotations

import uuid

from loguru import logger
from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from app.database import AsyncSessionLocal
from app.models.race import Race
from app.services.strategy_service import build_strategy, get_next_or_latest_race
from app.telegram.formatters import format_race_analysis

# Bot uses fewer simulations than the API for snappy replies.
BOT_SIMULATIONS = 5000


async def analysis_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /analysis [race_id] – full strategy from the live engine."""
    if update.effective_message is None:
        return

    logger.info(f"/analysis from {update.effective_user}")
    args = context.args or []

    await update.effective_message.reply_text("Analyzing race data…")

    async with AsyncSessionLocal() as session:
        race = None
        if args:
            try:
                race = (
                    await session.execute(
                        select(Race).where(Race.id == uuid.UUID(args[0]))
                    )
                ).scalar_one_or_none()
            except (ValueError, TypeError):
                race = None
        if race is None:
            race = await get_next_or_latest_race(session)

        if race is None:
            await update.effective_message.reply_text(
                "No races in the database yet. Run the data pipeline first:\n"
                "docker exec bf1_api python -m scripts.pipeline"
            )
            return

        strategy = await build_strategy(
            session, race, aggressiveness="balanced", n_simulations=BOT_SIMULATIONS
        )

    analysis = _strategy_to_analysis(strategy)
    text = format_race_analysis(analysis)
    await update.effective_message.reply_text(text, parse_mode="MarkdownV2")


def _strategy_to_analysis(strategy: dict) -> dict:
    """Map the engine strategy dict into the formatter's expected shape."""
    race = strategy.get("race", {})
    details = strategy.get("driver_details", [])
    top = sorted(details, key=lambda d: d.get("expected_value", 0), reverse=True)[:5]

    return {
        "race_name": race.get("name", "Race"),
        "round": race.get("round", "?"),
        "circuit": race.get("circuit", "TBD"),
        "date": race.get("date", "TBD")[:10],
        "weather": strategy.get("weather", {}),
        "top_drivers": [
            {
                "code": d.get("driver_code", "???"),
                "ev": d.get("expected_value", 0),
                "top3": d.get("top3_probability", 0),
                "dnf": d.get("dnf_probability", 0),
            }
            for d in top
        ],
        "strategy_type": strategy.get("strategy_type", "balanced"),
        "allocation": {
            d.get("driver_code", "???"): d.get("tokens", 0) for d in details
        },
        "insights": strategy.get("insights", []),
    }
