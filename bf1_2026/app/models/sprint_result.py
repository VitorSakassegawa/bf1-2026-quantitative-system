"""Sprint result model – sprint race positions and points."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SprintResult(Base):
    __tablename__ = "sprint_results"

    __table_args__ = (
        UniqueConstraint("race_id", "driver_id", name="uq_sprint_results_race_driver"),
        Index("ix_sprint_results_race_id", "race_id"),
        Index("ix_sprint_results_driver_id", "driver_id"),
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
    # The team the driver raced for in THIS session. Without it every past
    # result was attributed to the driver's current team (Hamilton's Mercedes
    # years counted for Ferrari), and a mid-season substitute had no team.
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id"), nullable=True
    )
    final_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    grid_position: Mapped[int] = mapped_column(Integer, nullable=False)
    points_scored: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    dnf: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    dnf_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
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
