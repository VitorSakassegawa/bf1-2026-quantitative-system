"""Prediction model – per-driver predictions for a race."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    race_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("races.id"), nullable=False
    )
    driver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id"), nullable=False
    )
    top3_probability: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    top10_probability: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    dnf_probability: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    expected_points: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    expected_value: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    recommended_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    model_version: Mapped[str] = mapped_column(String(50), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    race: Mapped["Race"] = relationship(back_populates="predictions")  # noqa: F821
    driver: Mapped["Driver"] = relationship(back_populates="predictions")  # noqa: F821

    def __repr__(self) -> str:
        return (
            f"<Prediction top3={self.top3_probability:.1%} "
            f"EV={self.expected_value:.2f}>"
        )
