"""Simulation handler – real Monte Carlo results from the engine."""

from __future__ import annotations

import uuid

from loguru import logger
from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from app.database import AsyncSessionLocal
from app.models.race import Race
from app.services.strategy_service import build_strategy, get_next_or_latest_race
from app.telegram.formatters import escape_md

BOT_SIMULATIONS = 8000


async def simulate_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /simulate [race_id] – run Monte Carlo and show the distribution."""
    if update.effective_message is None:
        return

    logger.info(f"/simulate from {update.effective_user}")
    args = context.args or []

    await update.effective_message.reply_text(
        f"Running {BOT_SIMULATIONS:,} simulations…"
    )

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
                "No races yet. Run: docker exec bf1_api python -m scripts.pipeline"
            )
            return

        strategy = await build_strategy(
            session, race, n_simulations=BOT_SIMULATIONS
        )

    details = sorted(
        strategy.get("driver_details", []),
        key=lambda d: d.get("expected_position", 20),
    )
    race_info = strategy.get("race", {})
    pit = strategy.get("pit_stop_projection", {})

    lines = [
        "━━━━━━━━━━━━━━━━━",
        "🎲 *MONTE CARLO*",
        f"{escape_md(race_info.get('name', 'Race'))} \\| {BOT_SIMULATIONS:,} sims",
        "━━━━━━━━━━━━━━━━━",
        "",
        "*Predicted finishing order \\(top 10\\):*",
    ]
    for i, d in enumerate(details[:10], 1):
        top3 = d.get("top3_probability", 0)
        bar_len = int(top3 * 20)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        lines.append(
            f"`{i:>2} {escape_md(d.get('driver_code', '???')):<3} "
            f"{bar} {top3:.0%}`"
        )

    if pit:
        lines.append("")
        lines.append(
            f"🛞 Pit strategy: *{escape_md(pit.get('label', 'n/a'))}*"
        )
        if pit.get("capped"):
            lines.append("_\\(degradation signal capped to realistic range\\)_")

    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode="MarkdownV2"
    )
