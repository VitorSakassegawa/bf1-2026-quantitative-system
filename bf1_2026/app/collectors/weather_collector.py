"""Weather data collector via OpenWeatherMap API."""

from __future__ import annotations

from datetime import datetime

import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from app.collectors.cache_manager import cached
from app.config import settings


class WeatherCollector:
    """Collect weather forecasts from OpenWeatherMap."""

    BASE_URL = "https://api.openweathermap.org/data/3.0/onecall"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        return self._client

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def _fetch(self, params: dict) -> dict:
        client = await self._get_client()
        params["appid"] = settings.openweather_api_key
        params["units"] = "metric"
        resp = await client.get(self.BASE_URL, params=params)
        resp.raise_for_status()
        return resp.json()

    @cached(ttl=1800)
    async def fetch_race_weather(
        self, lat: float, lon: float, race_date: datetime
    ) -> dict:
        """
        Fetch detailed weather forecast for a race location and date.

        Returns a normalized dict with rain, temperature, humidity, wind, etc.
        """
        try:
            data = await self._fetch(
                {"lat": lat, "lon": lon, "exclude": "minutely,alerts"}
            )

            # Try to find the hourly forecast closest to race time
            target_ts = race_date.timestamp()
            best_hour = None
            best_diff = float("inf")

            for hour in data.get("hourly", []):
                diff = abs(hour["dt"] - target_ts)
                if diff < best_diff:
                    best_diff = diff
                    best_hour = hour

            if best_hour is None:
                # Fall back to daily
                for day in data.get("daily", []):
                    diff = abs(day["dt"] - target_ts)
                    if diff < best_diff:
                        best_diff = diff
                        best_hour = day

            if best_hour is None:
                logger.warning("No forecast data found for race date")
                return self._empty_weather()

            rain_prob = best_hour.get("pop", 0.0)
            rain_volume = best_hour.get("rain", {})
            if isinstance(rain_volume, dict):
                rain_volume = rain_volume.get("1h", 0.0)
            elif not isinstance(rain_volume, (int, float)):
                rain_volume = 0.0

            temp_air = best_hour.get("temp", 20.0)
            if isinstance(temp_air, dict):
                temp_air = temp_air.get("day", 20.0)

            humidity = best_hour.get("humidity", 50.0)
            wind_speed = best_hour.get("wind_speed", 0.0) * 3.6  # m/s → km/h
            clouds = best_hour.get("clouds", 50) / 100.0

            track_temp = self.estimate_track_temperature(temp_air, clouds, 1.0)
            risk = self.calculate_weather_risk_index(
                {
                    "rain_probability": rain_prob,
                    "rain_volume_mm": rain_volume,
                    "humidity": humidity / 100.0,
                }
            )

            result = {
                "rain_probability": rain_prob,
                "rain_volume_mm": rain_volume,
                "temperature_air": temp_air,
                "temperature_track_estimated": track_temp,
                "humidity": humidity,
                "wind_speed_kmh": wind_speed,
                "weather_risk_index": risk,
                "cloud_cover": clouds,
            }

            logger.info(
                f"Weather fetched: rain={rain_prob:.0%}, "
                f"temp={temp_air:.1f}C, risk={risk:.2f}"
            )
            return result

        except Exception as e:
            logger.error(f"Weather fetch failed: {e}")
            return self._empty_weather()

    @staticmethod
    def calculate_weather_risk_index(weather_data: dict) -> float:
        """
        Composite weather risk index (0.0 – 1.0).

        risk = 0.4 * rain_probability
             + 0.3 * rain_volume_normalized
             + 0.3 * humidity
        """
        rain_prob = weather_data.get("rain_probability", 0.0)
        rain_volume = weather_data.get("rain_volume_mm", 0.0)
        humidity = weather_data.get("humidity", 0.0)

        rain_volume_norm = min(rain_volume / 50.0, 1.0)

        risk = 0.4 * rain_prob + 0.3 * rain_volume_norm + 0.3 * humidity
        return max(0.0, min(1.0, risk))

    @staticmethod
    def estimate_track_temperature(
        air_temp: float, cloud_cover: float, solar_radiation: float = 1.0
    ) -> float:
        """
        Empirical track temperature estimate.

        track_temp ≈ air_temp * (1.4 - 0.2 * cloud_factor)
        Clamped between air_temp and air_temp * 1.6.
        """
        cloud_factor = cloud_cover * solar_radiation
        multiplier = 1.4 - 0.2 * cloud_factor
        track_temp = air_temp * multiplier
        return max(air_temp, min(track_temp, air_temp * 1.6))

    @staticmethod
    def _empty_weather() -> dict:
        return {
            "rain_probability": 0.0,
            "rain_volume_mm": 0.0,
            "temperature_air": 25.0,
            "temperature_track_estimated": 35.0,
            "humidity": 50.0,
            "wind_speed_kmh": 10.0,
            "weather_risk_index": 0.15,
            "cloud_cover": 0.5,
        }

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
