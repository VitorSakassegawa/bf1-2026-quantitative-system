"""Bet handler – record the recommended bet so it can be scored later.

This does NOT place a bet on the pool's site; that is done by you, there.
/bet shows the recommendation for the next race and, on confirmation, stores
it here. After the race the nightly job scores it with the pool's rules, which
builds a track record of what following the engine would have earned.
"""

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
from app.telegram.formatters import escape_md, format_strategy
from app.telegram.handlers.common import NO_DATA_MESSAGE, resolve_race, strategy_for
from app.utils.validators import is_bet_deadline_passed

BET_SELECT_RACE, BET_SELECT_STRATEGY, BET_CONFIRM = range(3)


async def bet_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /bet – show the recommended bet and ask whether to record it."""
    if update.effective_message is None:
        return ConversationHandler.END
    logger.info(f"/bet from {update.effective_user}")

    profile = (context.user_data or {}).get("aggressiveness", "balanced")
    async with AsyncSessionLocal() as session:
        race = await resolve_race(session, [])
        if race is None:
            await update.effective_message.reply_text(NO_DATA_MESSAGE)
            return ConversationHandler.END
        strategy, pool = await strategy_for(session, race, profile)

    if strategy.get("constraint_errors") or not strategy.get("allocation"):
        await update.effective_message.reply_text(
            "Não foi possível montar uma aposta válida: "
            + "; ".join(strategy.get("constraint_errors") or ["sem pilotos suficientes"])
        )
        return ConversationHandler.END

    pick = strategy.get("eleventh_pick") or {}
    context.user_data.update(  # type: ignore[union-attr]
        {
            "race_id": strategy["race"]["id"],
            "allocation": dict(strategy["allocation"]),
            "eleventh": pick.get("driver_id"),
            "expected_value": strategy.get("total_ev", 0.0),
            "strategy": strategy.get("strategy_type", profile),
        }
    )
    keyboard = [
        [InlineKeyboardButton("Registrar esta aposta", callback_data="confirm_yes")],
        [InlineKeyboardButton("Cancelar", callback_data="confirm_no")],
    ]
    await update.effective_message.reply_text(
        format_strategy(strategy, pool)
        + "\n\n"
        + escape_md(
            "Registrar guarda esta aposta aqui para ser apurada depois da prova. "
            "A aposta que vale é a que você fizer no site do bolão."
        ),
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return BET_CONFIRM


async def bet_select_race(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Kept for handler-registration compatibility; the race is auto-selected."""
    return BET_CONFIRM


async def bet_select_strategy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Kept for handler-registration compatibility; there is one recommendation."""
    return BET_CONFIRM


async def bet_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store the confirmed bet, replacing an earlier one for the same race."""
    query = update.callback_query
    if query is None:
        return ConversationHandler.END
    await query.answer()

    if query.data != "confirm_yes":
        await query.edit_message_reply_markup(reply_markup=None)
        return ConversationHandler.END

    data = context.user_data or {}
    race_id, allocation = data.get("race_id"), data.get("allocation") or {}
    tg_user = update.effective_user
    if not race_id or not allocation or tg_user is None:
        await query.edit_message_reply_markup(reply_markup=None)
        return ConversationHandler.END

    async with AsyncSessionLocal() as session:
        race = (
            await session.execute(select(Race).where(Race.id == uuid.UUID(race_id)))
        ).scalar_one_or_none()
        if race is None:
            await query.edit_message_text("Corrida não encontrada.")
            return ConversationHandler.END
        if is_bet_deadline_passed(race.race_date, race.deadline_bets):
            await query.edit_message_text("⏰ O prazo desta prova já encerrou.")
            return ConversationHandler.END

        user = (
            await session.execute(select(User).where(User.telegram_id == tg_user.id))
        ).scalar_one_or_none()
        if user is None:
            user = User(telegram_id=tg_user.id, username=tg_user.username)
            session.add(user)
            await session.flush()

        # As on the pool, the last bet before the deadline is the one that counts.
        bet = (
            await session.execute(
                select(Bet).where(Bet.user_id == user.id, Bet.race_id == race.id)
            )
        ).scalar_one_or_none()
        if bet is None:
            bet = Bet(user_id=user.id, race_id=race.id, allocations={})
            session.add(bet)
            user.total_bets += 1
        try:
            bet.strategy_type = StrategyType(data.get("strategy", "balanced"))
        except ValueError:
            bet.strategy_type = StrategyType.balanced
        bet.allocations = allocation
        bet.eleventh_driver_id = uuid.UUID(data["eleventh"]) if data.get("eleventh") else None
        bet.expected_value = float(data.get("expected_value", 0.0))
        bet.confirmed_at = datetime.now(timezone.utc)
        bet.status = BetStatus.confirmed
        bet.actual_points = None
        bet.eleventh_hit = None
        await session.commit()

    logger.info(f"Bet recorded by {tg_user.id} on {race_id}")
    await query.edit_message_reply_markup(reply_markup=None)
    if update.effective_message:
        await update.effective_message.reply_text(
            "✅ Aposta registrada. Não esqueça de lançar no site do bolão."
        )
    return ConversationHandler.END


async def bet_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /cancel during the bet flow."""
    if update.effective_message:
        await update.effective_message.reply_text("Cancelado.")
    return ConversationHandler.END
