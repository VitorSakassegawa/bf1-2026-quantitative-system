"""Alert engine – sends Telegram notifications for race events."""

from __future__ import annotations

from loguru import logger
from telegram import Bot

from app.config import settings
from app.telegram.formatters import format_alert_message


class AlertEngine:
    """Sends automated Telegram alerts to subscribed users."""

    def __init__(self) -> None:
        self._bot: Bot | None = None

    def _get_bot(self) -> Bot:
        if self._bot is None:
            if not settings.telegram_bot_token:
                raise ValueError("TELEGRAM_BOT_TOKEN not configured")
            self._bot = Bot(token=settings.telegram_bot_token)
        return self._bot

    async def _send_to_subscribers(
        self, message: str, subscribers: list[int]
    ) -> int:
        """Send a message to all subscribers. Returns count of successful sends."""
        bot = self._get_bot()
        sent = 0
        for chat_id in subscribers:
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode="MarkdownV2",
                )
                sent += 1
            except Exception as e:
                logger.warning(f"Failed to send alert to {chat_id}: {e}")
        logger.info(f"Alert sent to {sent}/{len(subscribers)} subscribers")
        return sent

    async def send_race_reminder_24h(
        self, race: dict, subscribers: list[int]
    ) -> None:
        """24h before race: analysis preview + strategy suggestion."""
        message = format_alert_message("race_24h", race)
        await self._send_to_subscribers(message, subscribers)

    async def send_race_reminder_1h(
        self, race: dict, subscribers: list[int]
    ) -> None:
        """1h before race: LAST CHANCE to bet + current grid + weather."""
        message = format_alert_message("race_1h", race)
        await self._send_to_subscribers(message, subscribers)

    async def send_weather_alert(
        self, race: dict, weather: dict, subscribers: list[int]
    ) -> None:
        """Alert when weather_risk_index > 0.7."""
        data = {**race, **weather}
        message = format_alert_message("weather", data)
        await self._send_to_subscribers(message, subscribers)

    async def send_grid_change_alert(
        self, race: dict, changes: list[dict], subscribers: list[int]
    ) -> None:
        """Alert on unexpected grid changes (penalties, car swaps)."""
        from app.telegram.formatters import escape_md

        lines = ["⚡ *GRID CHANGE*\n"]
        for change in changes:
            lines.append(
                f"  {escape_md(change.get('driver', '???'))}: "
                f"{escape_md(change.get('description', 'change'))}"
            )
        lines.append("\nReview your strategy with /analysis")
        message = "\n".join(lines)
        await self._send_to_subscribers(message, subscribers)

    async def send_race_result(
        self,
        race: dict,
        results: dict,
        user_bets: list[dict],
        subscribers: list[int],
    ) -> None:
        """Post-race: results + points gained/lost + new standings."""
        data = {
            "race_name": race.get("race_name", "Race"),
            "points": sum(b.get("actual_points", 0) for b in user_bets),
        }
        message = format_alert_message("result", data)
        await self._send_to_subscribers(message, subscribers)
