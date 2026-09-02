"""Scheduled jobs – automated data updates, alerts, and model retraining.

Every job here used to be a stub: `daily_data_update` fetched the calendar and
logged its length, `check_race_alerts` was `pass`, `update_weather` constructed
a collector and closed it, and `weekly_retrain` built a model object and logged
"completed" without training. The real work sat behind "In production:"
comments, so the weather table was never populated, no reminder was ever sent,
and the model was never retrained — while the logs reported success.

All four now do the work, and all four are guarded so one failure cannot take
the scheduler process down.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.circuit import Circuit
from app.models.race import Race, RaceStatus
from app.models.user import User
from app.models.weather import Weather, WeatherSource
from app.utils.validators import bet_deadline_for

# Cron fields are wall-clock, so an unset timezone resolves against whatever
# the container happens to be set to. Pin it: the schedule must not move when
# the base image or host clock changes.
scheduler = AsyncIOScheduler(timezone=timezone.utc)

# How close to a race we consider it "upcoming" for weather and alerts.
WEATHER_HORIZON_DAYS = 7
ALERT_24H_WINDOW = timedelta(hours=1)
ALERT_1H_WINDOW = timedelta(minutes=30)


async def _upcoming_races(session, within_days: int) -> list[Race]:
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=within_days)
    return list(
        (
            await session.execute(
                select(Race)
                .where(
                    Race.race_date >= now,
                    Race.race_date <= horizon,
                    Race.status == RaceStatus.scheduled,
                )
                .order_by(Race.race_date)
            )
        )
        .scalars()
        .all()
    )


async def _subscriber_chat_ids(session) -> list[int]:
    rows = (await session.execute(select(User.telegram_id))).all()
    return [int(r[0]) for r in rows if r[0] is not None]


@scheduler.scheduled_job("cron", hour=2, minute=0, id="daily_data_update")
async def daily_data_update() -> None:
    """Daily 02:00 UTC: ingest finished races, then rebuild ELO and KPIs."""
    logger.info("Starting daily data update job")
    try:
        from scripts.build_intelligence import build
        from scripts.ingest import ingest

        season = datetime.now(timezone.utc).year

        # ingest() skips rounds already stored and, since the review, refuses
        # to write a race whose results came back empty — so re-running it is
        # how new results get picked up.
        await ingest(season, season)
        await build()

        logger.info(f"Daily data update completed for season {season}")
    except Exception as e:
        logger.exception(f"Daily data update failed: {e}")


@scheduler.scheduled_job("cron", minute="*/30", id="check_race_alerts")
async def check_race_alerts() -> None:
    """Every 30 minutes: 24h and 1h reminders for upcoming races."""
    logger.debug("Checking race alerts")
    if not settings.telegram_bot_token:
        logger.debug("No TELEGRAM_BOT_TOKEN configured; skipping race alerts")
        return

    try:
        from app.alerts.alert_engine import AlertEngine

        engine = AlertEngine()
        now = datetime.now(timezone.utc)

        async with AsyncSessionLocal() as session:
            races = await _upcoming_races(session, within_days=2)
            if not races:
                return
            subscribers = await _subscriber_chat_ids(session)
            if not subscribers:
                logger.debug("No subscribers registered; nothing to send")
                return

            for race in races:
                race_date = race.race_date
                if race_date.tzinfo is None:
                    race_date = race_date.replace(tzinfo=timezone.utc)
                until = race_date - now

                payload = {
                    "race_name": race.race_name,
                    "round": race.round_number,
                    "date": race_date.isoformat(),
                    "deadline": bet_deadline_for(
                        race_date, race.deadline_bets
                    ).isoformat(),
                }

                # The job runs every 30 minutes, so each window is matched once.
                if abs(until - timedelta(hours=24)) <= ALERT_24H_WINDOW:
                    await engine.send_race_reminder_24h(payload, subscribers)
                    logger.info(f"Sent 24h reminder for {race.race_name}")
                elif abs(until - timedelta(hours=1)) <= ALERT_1H_WINDOW:
                    await engine.send_race_reminder_1h(payload, subscribers)
                    logger.info(f"Sent 1h reminder for {race.race_name}")
    except Exception as e:
        logger.exception(f"Race alert check failed: {e}")


@scheduler.scheduled_job("cron", hour=8, minute=0, id="update_weather")
async def update_weather() -> None:
    """Daily 08:00 UTC: refresh forecasts for races inside the horizon.

    `fetch_race_weather` had no caller anywhere in the codebase, so the weather
    table was always empty and strategy_service always fell back to its
    hardcoded defaults.
    """
    logger.info("Starting weather update job")
    if not settings.openweather_api_key:
        logger.info("No OPENWEATHER_API_KEY configured; skipping weather update")
        return

    collector = None
    try:
        from app.collectors.weather_collector import WeatherCollector

        collector = WeatherCollector()
        updated = 0

        async with AsyncSessionLocal() as session:
            races = await _upcoming_races(session, WEATHER_HORIZON_DAYS)
            for race in races:
                circuit = (
                    await session.execute(
                        select(Circuit).where(Circuit.id == race.circuit_id)
                    )
                ).scalar_one_or_none()
                if circuit is None or circuit.latitude is None or circuit.longitude is None:
                    logger.debug(
                        f"{race.race_name}: circuit has no coordinates, skipping"
                    )
                    continue

                race_date = race.race_date
                if race_date.tzinfo is None:
                    race_date = race_date.replace(tzinfo=timezone.utc)

                data = await collector.fetch_race_weather(
                    circuit.latitude, circuit.longitude, race_date
                )
                if not data:
                    continue

                session.add(
                    Weather(
                        race_id=race.id,
                        timestamp=datetime.now(timezone.utc),
                        rain_probability=data.get("rain_probability", 0.0),
                        rain_volume_mm=data.get("rain_volume_mm", 0.0),
                        temperature_air=data.get("temperature_air", 20.0),
                        temperature_track_estimated=data.get(
                            "temperature_track_estimated"
                        ),
                        humidity=data.get("humidity", 0.0),
                        wind_speed_kmh=data.get("wind_speed_kmh", 0.0),
                        weather_risk_index=collector.calculate_weather_risk_index(data),
                        source=WeatherSource.openweather,
                    )
                )
                updated += 1

            await session.commit()

        logger.info(f"Weather update completed for {updated} race(s)")
    except Exception as e:
        logger.exception(f"Weather update failed: {e}")
    finally:
        if collector is not None:
            try:
                await collector.close()
            except Exception:
                pass


@scheduler.scheduled_job("cron", day_of_week="mon", hour=3, id="weekly_retrain")
async def weekly_model_retrain() -> None:
    """Monday 03:00 UTC: full model retrain from the ingested history."""
    logger.info("Starting weekly model retrain")
    try:
        from scripts.train_model import train

        # train() builds the dataset, fits position and DNF models, and saves a
        # versioned artifact via the registry. It logs and returns early when
        # there are too few rows rather than raising.
        await train()
        logger.info("Weekly retrain completed")
    except Exception as e:
        logger.exception(f"Weekly retrain failed: {e}")


def start_scheduler() -> None:
    """Start the APScheduler."""
    scheduler.start()
    logger.info(
        "Scheduler started (UTC) with jobs: daily_data_update, "
        "check_race_alerts, update_weather, weekly_retrain"
    )


def stop_scheduler() -> None:
    """Gracefully stop the scheduler."""
    scheduler.shutdown()
    logger.info("Scheduler stopped")
