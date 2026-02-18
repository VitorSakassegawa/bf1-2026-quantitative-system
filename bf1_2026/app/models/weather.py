"""Weather model – climate data for race weekends."""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Float, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class WeatherSource(str, enum.Enum):
    openweather = "openweather"
    manual = "manual"
    historical = "historical"


class Weather(Base):
    __tablename__ = "weather"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    race_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("races.id"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    rain_probability: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    rain_volume_mm: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    temperature_air: Mapped[float] = mapped_column(Float, nullable=False)
    temperature_track_estimated: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    humidity: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    wind_speed_kmh: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    weather_risk_index: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )
    source: Mapped[WeatherSource] = mapped_column(
        Enum(WeatherSource), default=WeatherSource.openweather, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    race: Mapped["Race"] = relationship(back_populates="weather_data")  # noqa: F821

    def __repr__(self) -> str:
        return f"<Weather rain={self.rain_probability:.0%} risk={self.weather_risk_index:.2f}>"
