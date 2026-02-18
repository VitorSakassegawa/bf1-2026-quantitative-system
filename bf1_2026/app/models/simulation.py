"""Simulation model – Monte Carlo simulation runs."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Simulation(Base):
    __tablename__ = "simulations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    race_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("races.id"), nullable=False
    )
    n_simulations: Mapped[int] = mapped_column(
        Integer, default=20000, nullable=False
    )
    results: Mapped[dict] = mapped_column(JSONB, nullable=False)
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    model_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    race: Mapped["Race"] = relationship(back_populates="simulations")  # noqa: F821

    def __repr__(self) -> str:
        return f"<Simulation n={self.n_simulations} v={self.model_version}>"
