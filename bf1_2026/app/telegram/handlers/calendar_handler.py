"""Calendar handler – show the 2026 race calendar."""

from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes


# 2026 F1 Calendar (placeholder – updated when real calendar is available)
CALENDAR_2026 = [
    {"round": 1, "name": "Australian GP", "date": "2026-03-15", "sprint": False},
    {"round": 2, "name": "Chinese GP", "date": "2026-03-29", "sprint": True},
    {"round": 3, "name": "Japanese GP", "date": "2026-04-05", "sprint": False},
    {"round": 4, "name": "Bahrain GP", "date": "2026-04-19", "sprint": False},
    {"round": 5, "name": "Saudi Arabian GP", "date": "2026-04-26", "sprint": False},
    {"round": 6, "name": "Miami GP", "date": "2026-05-10", "sprint": True},
    {"round": 7, "name": "Emilia Romagna GP", "date": "2026-05-24", "sprint": False},
    {"round": 8, "name": "Monaco GP", "date": "2026-05-31", "sprint": False},
    {"round": 9, "name": "Spanish GP", "date": "2026-06-14", "sprint": False},
    {"round": 10, "name": "Canadian GP", "date": "2026-06-28", "sprint": False},
    {"round": 11, "name": "Austrian GP", "date": "2026-07-05", "sprint": True},
    {"round": 12, "name": "British GP", "date": "2026-07-19", "sprint": False},
    {"round": 13, "name": "Belgian GP", "date": "2026-07-26", "sprint": False},
    {"round": 14, "name": "Hungarian GP", "date": "2026-08-02", "sprint": False},
    {"round": 15, "name": "Dutch GP", "date": "2026-08-30", "sprint": False},
    {"round": 16, "name": "Italian GP", "date": "2026-09-06", "sprint": False},
    {"round": 17, "name": "Azerbaijan GP", "date": "2026-09-20", "sprint": True},
    {"round": 18, "name": "Singapore GP", "date": "2026-10-04", "sprint": False},
    {"round": 19, "name": "US GP", "date": "2026-10-18", "sprint": True},
    {"round": 20, "name": "Mexican GP", "date": "2026-10-25", "sprint": False},
    {"round": 21, "name": "Brazilian GP", "date": "2026-11-08", "sprint": True},
    {"round": 22, "name": "Las Vegas GP", "date": "2026-11-22", "sprint": False},
    {"round": 23, "name": "Qatar GP", "date": "2026-11-29", "sprint": False},
    {"round": 24, "name": "Abu Dhabi GP", "date": "2026-12-06", "sprint": False},
]


async def calendar_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /calendar – display the 2026 race calendar."""
    if update.effective_message is None:
        return

    logger.info(f"/calendar from {update.effective_user}")
    now = datetime.now(timezone.utc)

    lines = ["🏎 *F1 2026 CALENDAR*\n"]
    next_found = False

    for race in CALENDAR_2026:
        race_date = datetime.strptime(race["date"], "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
        sprint_tag = " 🟡 Sprint" if race["sprint"] else ""

        if race_date < now:
            icon = "🔵"
        elif not next_found:
            icon = "🟢"
            next_found = True
            days_until = (race_date - now).days
            sprint_tag += f" \\({days_until}d\\)"
        else:
            icon = "⚪"

        line = (
            f"{icon} R{race['round']:02d} \\| "
            f"{_esc(race['date'])} \\| "
            f"{_esc(race['name'])}{_esc(sprint_tag)}"
        )
        lines.append(line)

    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode="MarkdownV2"
    )


def _esc(text: str) -> str:
    """Escape MarkdownV2 special chars."""
    special = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in text)
