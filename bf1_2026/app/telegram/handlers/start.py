"""Start command handler – welcome message and user registration."""

from __future__ import annotations

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start and /help – welcome message + main menu."""
    if update.effective_user is None or update.effective_message is None:
        return

    user = update.effective_user
    logger.info(f"/start from {user.username} ({user.id})")

    welcome = (
        f"Olá, {_escape_md(user.first_name)}\\! Sou o assistente do *BF1\\-2026*\\.\n"
        "\n"
        "Simulo a corrida a partir do grid de largada e monto a aposta de maior "
        "pontuação esperada pelas regras do bolão: 15 fichas, um piloto por "
        "equipe, e o palpite do 11º lugar\\.\n"
        "\n"
        "*Comandos*\n"
        "/analysis \\- aposta recomendada para a próxima prova\n"
        "/simulate \\- chances de cada piloto \\(vitória, pódio, 11º, abandono\\)\n"
        "/classificacao \\- classificação do bolão e sua posição\n"
        "/calendar \\- calendário da temporada\n"
        "/bet \\- registrar a aposta recomendada para apurar depois\n"
        "/settings \\- perfil de risco\n"
        "\n"
        "O grid de largada é o dado mais importante: rode /analysis de novo "
        "depois da classificação\\."
    )

    await update.effective_message.reply_text(welcome, parse_mode="MarkdownV2")


def _escape_md(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    special = r"_*[]()~`>#+-=|{}.!"
    result = []
    for ch in text:
        if ch in special:
            result.append(f"\\{ch}")
        else:
            result.append(ch)
    return "".join(result)
