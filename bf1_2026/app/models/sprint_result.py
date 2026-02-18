"""Sprint result model – sprint race positions and points."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SprintResult(Base):
    __tablename__ = "sprint_results"

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
    dnf: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    race: Mapped["Race"] = relationship(back_populates="sprint_results")  # noqa: F821
    driver: Mapped["Driver"] = relationship(  # noqa: F821
        back_populates="sprint_results"
    )

    def __repr__(self) -> str:
        pos = f"P{self.final_position}" if self.final_position else "DNF"
        return f"<SprintResult {pos}>"
