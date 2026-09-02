"""Bet handler – real allocation from the engine, persisted to the DB."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from app.database import AsyncSessionLocal
from app.models.bet import Bet, BetStatus, StrategyType
from app.models.race import Race
from app.models.user import User
from app.services.strategy_service import build_strategy, get_next_or_latest_race
from app.telegram.formatters import escape_md
from app.utils.validators import is_bet_deadline_passed

BET_SELECT_RACE = 0
BET_SELECT_STRATEGY = 1
BET_CONFIRM = 2

BOT_SIMULATIONS = 5000


async def bet_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /bet – pick the upcoming race and offer strategy choices."""
    if update.effective_message is None:
        return ConversationHandler.END

    logger.info(f"/bet from {update.effective_user}")

    async with AsyncSessionLocal() as session:
        race = await get_next_or_latest_race(session)

    if race is None:
        await update.effective_message.reply_text(
            "No races yet. Run: docker exec bf1_api python -m scripts.pipeline"
        )
        return ConversationHandler.END

    context.user_data["race_id"] = str(race.id)  # type: ignore[index]
    context.user_data["race_name"] = race.race_name  # type: ignore[index]

    keyboard = [
        [InlineKeyboardButton("Conservative", callback_data="strat_conservative")],
        [InlineKeyboardButton("Balanced (Recommended)", callback_data="strat_balanced")],
        [InlineKeyboardButton("Aggressive", callback_data="strat_aggressive")],
        [InlineKeyboardButton("Ultra Aggressive", callback_data="strat_ultra_aggressive")],
    ]
    await update.effective_message.reply_text(
        f"Race: {race.race_name}\nChoose your strategy:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return BET_SELECT_STRATEGY


async def bet_select_race(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Kept for handler-registration compatibility (race auto-selected in bet_start)."""
    return BET_SELECT_STRATEGY


async def bet_select_strategy(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Build the real allocation for the chosen strategy and show it."""
    query = update.callback_query
    if query is None:
        return ConversationHandler.END
    await query.answer()

    strategy_type = query.data.replace("strat_", "")  # type: ignore[union-attr]
    context.user_data["strategy"] = strategy_type  # type: ignore[index]
    race_id = context.user_data.get("race_id")  # type: ignore[union-attr]

    await query.edit_message_text("Optimizing your 15 tokens…")

    async with AsyncSessionLocal() as session:
        race = (
            await session.execute(select(Race).where(Race.id == uuid.UUID(race_id)))
        ).scalar_one_or_none()
        if race is None:
            await query.edit_message_text("Race not found.")
            return ConversationHandler.END
        strategy = await build_strategy(
            session, race, aggressiveness=strategy_type, n_simulations=BOT_SIMULATIONS
        )

    details = strategy.get("driver_details", [])
    context.user_data["allocation"] = {  # type: ignore[index]
        d["driver_id"]: d["tokens"] for d in details
    }
    context.user_data["expected_value"] = strategy.get("total_ev", 0)  # type: ignore[index]

    lines = [f"Strategy: *{escape_md(strategy_type.upper())}*", ""]
    for d in sorted(details, key=lambda x: x["tokens"], reverse=True):
        bar = "●" * d["tokens"] + "○" * (5 - d["tokens"])
        lines.append(
            f"  {escape_md(d['driver_code'])}: \\[{escape_md(bar)}\\] {d['tokens']}T"
        )
    lines.append("")
    # Escape the decimal point: an unescaped "." breaks MarkdownV2 parsing and
    # Telegram rejects the message with 400, so /bet silently dead-ends.
    total_ev = escape_md(f"{strategy.get('total_ev', 0):.1f}")
    lines.append(f"Total EV: {total_ev} \\| 15 tokens")

    keyboard = [
        [InlineKeyboardButton("Confirm", callback_data="confirm_yes")],
        [InlineKeyboardButton("Cancel", callback_data="confirm_no")],
    ]
    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return BET_CONFIRM


async def bet_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Persist the confirmed bet (enforcing the betting deadline)."""
    query = update.callback_query
    if query is None:
        return ConversationHandler.END
    await query.answer()

    if query.data != "confirm_yes":
        await query.edit_message_text("Bet cancelled\\.", parse_mode="MarkdownV2")
        return ConversationHandler.END

    race_id = context.user_data.get("race_id")  # type: ignore[union-attr]
    allocation = context.user_data.get("allocation", {})  # type: ignore[union-attr]
    strategy_type = context.user_data.get("strategy", "balanced")  # type: ignore[union-attr]
    expected_value = context.user_data.get("expected_value", 0)  # type: ignore[union-attr]
    tg_user = update.effective_user

    async with AsyncSessionLocal() as session:
        race = (
            await session.execute(select(Race).where(Race.id == uuid.UUID(race_id)))
        ).scalar_one_or_none()
        if race is None:
            await query.edit_message_text("Race not found.")
            return ConversationHandler.END

        if is_bet_deadline_passed(race.race_date, race.deadline_bets):
            await query.edit_message_text(
                "⏰ Betting is closed for this race (deadline is 1h before start)."
            )
            return ConversationHandler.END

        # Find or create the user.
        user = (
            await session.execute(
                select(User).where(User.telegram_id == tg_user.id)
            )
        ).scalar_one_or_none()
        if user is None:
            user = User(telegram_id=tg_user.id, username=tg_user.username)
            session.add(user)
            await session.flush()

        bet = Bet(
            user_id=user.id,
            race_id=race.id,
            strategy_type=StrategyType(strategy_type),
            allocations=allocation,
            expected_value=float(expected_value),
            confirmed_at=datetime.now(timezone.utc),
            status=BetStatus.confirmed,
        )
        session.add(bet)
        user.total_bets += 1
        await session.commit()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    logger.info(f"Bet confirmed by {tg_user.id}: {strategy_type} on {race_id}")
    await query.edit_message_text(
        f"✅ Bet confirmed at {escape_md(now)}\n"
        f"Strategy: {escape_md(strategy_type)}\n"
        f"Good luck\\! 🏎",
        parse_mode="MarkdownV2",
    )
    return ConversationHandler.END


async def bet_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /cancel during the bet flow."""
    if update.effective_message:
        await update.effective_message.reply_text("Bet cancelled.")
    return ConversationHandler.END
