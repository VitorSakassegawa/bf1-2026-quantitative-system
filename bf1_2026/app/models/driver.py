"""Driver model – F1 driver information and current ELO."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Driver(Base):
    __tablename__ = "drivers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(3), unique=True, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    nationality: Mapped[str] = mapped_column(String(100), nullable=False)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id"), nullable=False
    )
    current_elo: Mapped[float] = mapped_column(Float, default=1500.0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    team: Mapped["Team"] = relationship(back_populates="drivers")  # noqa: F821
    race_results: Mapped[list["RaceResult"]] = relationship(  # noqa: F821
        back_populates="driver"
    )
    qualifying_results: Mapped[list["QualifyingResult"]] = relationship(  # noqa: F821
        back_populates="driver"
    )
    sprint_results: Mapped[list["SprintResult"]] = relationship(  # noqa: F821
        back_populates="driver"
    )
    predictions: Mapped[list["Prediction"]] = relationship(  # noqa: F821
        back_populates="driver"
    )

    def __repr__(self) -> str:
        return f"<Driver {self.code} – {self.name} (ELO: {self.current_elo:.0f})>"
