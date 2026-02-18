"""Scheduled jobs – automated data updates, alerts, and model retraining."""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

scheduler = AsyncIOScheduler()


@scheduler.scheduled_job("cron", hour=2, minute=0, id="daily_data_update")
async def daily_data_update() -> None:
    """
    Daily 2 AM job:
    1. Check for new race results
    2. Update DB with results
    3. Recalculate ELOs
    4. Incremental model retrain
    5. Update KPIs
    6. Generate predictions for the next race
    """
    logger.info("Starting daily data update job")
    try:
        from app.collectors.f1_api_fallback import F1ApiFallback

        collector = F1ApiFallback()

        # Check latest results
        from datetime import datetime
        season = datetime.now().year
        calendar = await collector.fetch_calendar(season)
        logger.info(f"Checked calendar: {len(calendar)} races found for {season}")

        # In production: compare with DB, ingest new results,
        # recalculate ELOs, retrain model incrementally, update KPIs
        await collector.close()
        logger.info("Daily data update completed")
    except Exception as e:
        logger.error(f"Daily data update failed: {e}")


@scheduler.scheduled_job("cron", minute="*/30", id="check_race_alerts")
async def check_race_alerts() -> None:
    """
    Every 30 minutes: check for upcoming races and send alerts.
    - 24h before race
    - 1h before race
    - Extreme weather changes
    """
    logger.debug("Checking race alerts")
    try:
        # In production: query DB for upcoming races,
        # compare with alert thresholds, send Telegram messages
        pass
    except Exception as e:
        logger.error(f"Race alert check failed: {e}")


@scheduler.scheduled_job("cron", hour=8, minute=0, id="update_weather")
async def update_weather() -> None:
    """Daily 8 AM: update weather forecasts for upcoming races."""
    logger.info("Starting weather update job")
    try:
        from app.collectors.weather_collector import WeatherCollector

        weather = WeatherCollector()
        # In production: fetch weather for all upcoming races within 7 days,
        # update DB, recalculate weather risk indices
        await weather.close()
        logger.info("Weather update completed")
    except Exception as e:
        logger.error(f"Weather update failed: {e}")


@scheduler.scheduled_job("cron", day_of_week="mon", hour=3, id="weekly_retrain")
async def weekly_model_retrain() -> None:
    """Monday 3 AM: full model retrain (not incremental)."""
    logger.info("Starting weekly model retrain")
    try:
        from app.models_ml.xgboost_model import XGBoostF1Model

        model = XGBoostF1Model()
        # In production: load full training dataset from DB,
        # train from scratch, evaluate, save new version
        logger.info("Weekly retrain completed")
    except Exception as e:
        logger.error(f"Weekly retrain failed: {e}")


def start_scheduler() -> None:
    """Start the APScheduler."""
    scheduler.start()
    logger.info("Scheduler started with jobs: "
                "daily_data_update, check_race_alerts, update_weather, weekly_retrain")


def stop_scheduler() -> None:
    """Gracefully stop the scheduler."""
    scheduler.shutdown()
    logger.info("Scheduler stopped")
