"""Race result model – final positions, points, DNFs."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class RaceResult(Base):
    __tablename__ = "race_results"

    __table_args__ = (
        # One result per driver per race. Idempotency in scripts/ingest.py was
        # a SELECT-then-INSERT with nothing behind it, so a concurrent or
        # interrupted run could duplicate a whole race.
        UniqueConstraint("race_id", "driver_id", name="uq_race_results_race_driver"),
        # Postgres does not index foreign keys automatically. These two are the
        # hot paths: build_intelligence scans by race, and strategy_service
        # queries by driver once per driver on every strategy request.
        Index("ix_race_results_race_id", "race_id"),
        Index("ix_race_results_driver_id", "driver_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    race_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("races.id"), nullable=False
    )
    driver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id"), nullable=False
    )
    final_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    grid_position: Mapped[int] = mapped_column(Integer, nullable=False)
    points_scored: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    fastest_lap: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    dnf: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    dnf_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pit_stops: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    laps_completed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    race: Mapped["Race"] = relationship(back_populates="race_results")  # noqa: F821
    driver: Mapped["Driver"] = relationship(back_populates="race_results")  # noqa: F821

    def __repr__(self) -> str:
        pos = f"P{self.final_position}" if self.final_position else "DNF"
        return f"<RaceResult {pos}>"
