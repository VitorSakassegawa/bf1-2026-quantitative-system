"""Circuit model – F1 track information and KPIs."""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Float, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TrackType(str, enum.Enum):
    high_speed = "high_speed"
    technical = "technical"
    mixed = "mixed"


class Circuit(Base):
    __tablename__ = "circuits"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    country: Mapped[str] = mapped_column(String(100), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    track_type: Mapped[TrackType] = mapped_column(
        Enum(TrackType), default=TrackType.mixed, nullable=False
    )
    avg_overtaking_index: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_winner_grid_position: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_safety_cars: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_dnf_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    historical_rain_frequency: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_tire_degradation: Mapped[float | None] = mapped_column(Float, nullable=True)
    temperature_sensitivity: Mapped[float | None] = mapped_column(Float, nullable=True)
    lap_length_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_laps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    drs_zones: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    races: Mapped[list["Race"]] = relationship(back_populates="circuit")  # noqa: F821

    def __repr__(self) -> str:
        return f"<Circuit {self.name} ({self.country})>"
