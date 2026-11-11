"""Calendar handler – the season's calendar, read from the database."""

from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import func, select
from telegram import Update
from telegram.ext import ContextTypes

from app.database import AsyncSessionLocal
from app.models.race import Race, RaceStatus
from app.telegram.formatters import code, escape_md, local_time


async def calendar_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /calendar.

    The calendar used to be a list typed into this file, and it had drifted
    into fiction: 24 rounds including an Imola race, with most dates wrong.
    It now shows the races in the database, which the results sync keeps in
    step with the real calendar.
    """
    if update.effective_message is None:
        return
    logger.info(f"/calendar from {update.effective_user}")
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as session:
        season = await session.scalar(
            select(func.max(Race.season)).where(Race.season <= now.year)
        )
        races = (
            await session.execute(
                select(Race).where(Race.season == season).order_by(Race.round_number)
            )
        ).scalars().all()

    if not races:
        await update.effective_message.reply_text(
            "Calendário vazio. Rode: docker exec bf1_api python -m scripts.pipeline"
        )
        return

    lines = [f"🏎 *Calendário F1 {escape_md(season)}*", ""]
    next_found = False
    for race in races:
        start = race.race_date if race.race_date.tzinfo else race.race_date.replace(tzinfo=timezone.utc)
        if race.status == RaceStatus.cancelled:
            icon = "✖"
        elif start < now:
            icon = "✓"
        elif not next_found:
            icon = "▶"
            next_found = True
        else:
            icon = " "
        name = race.race_name.replace(" Grand Prix", "")[:22]
        sprint = " S" if race.is_sprint_weekend else ""
        lines.append(
            code(f"{icon} R{race.round_number:02d} {local_time(start, '%d/%m %H:%M')}  {name}{sprint}")
        )
    lines.append("")
    lines.append(escape_md("▶ próxima · S = fim de semana com sprint · horário de Brasília"))
    await update.effective_message.reply_text("\n".join(lines), parse_mode="MarkdownV2")
