"""Qualifying result model – grid positions and lap times."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class QualifyingResult(Base):
    __tablename__ = "qualifying_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    race_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("races.id"), nullable=False
    )
    driver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id"), nullable=False
    )
    grid_position: Mapped[int] = mapped_column(Integer, nullable=False)
    q1_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    q2_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    q3_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    gap_to_pole: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    race: Mapped["Race"] = relationship(back_populates="qualifying_results")  # noqa: F821
    driver: Mapped["Driver"] = relationship(  # noqa: F821
        back_populates="qualifying_results"
    )

    def __repr__(self) -> str:
        return f"<QualifyingResult P{self.grid_position}>"
