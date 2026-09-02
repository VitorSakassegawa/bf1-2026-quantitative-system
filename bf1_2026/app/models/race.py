"""Race model – individual Grand Prix event."""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class RaceStatus(str, enum.Enum):
    scheduled = "scheduled"
    qualifying_done = "qualifying_done"
    finished = "finished"
    cancelled = "cancelled"


class Race(Base):
    __tablename__ = "races"

    __table_args__ = (
        # A season/round pair identifies a Grand Prix. Both ingest.py and
        # seed.py rely on this being unique but nothing enforced it.
        UniqueConstraint("season", "round_number", name="uq_races_season_round"),
        Index("ix_races_circuit_id", "circuit_id"),
        Index("ix_races_season_round", "season", "round_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    circuit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("circuits.id"), nullable=False
    )
    season: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    race_name: Mapped[str] = mapped_column(String(255), nullable=False)
    race_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    qualifying_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sprint_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_sprint_weekend: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    status: Mapped[RaceStatus] = mapped_column(
        Enum(RaceStatus), default=RaceStatus.scheduled, nullable=False
    )
    deadline_bets: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    circuit: Mapped["Circuit"] = relationship(back_populates="races")  # noqa: F821
    race_results: Mapped[list["RaceResult"]] = relationship(  # noqa: F821
        back_populates="race"
    )
    qualifying_results: Mapped[list["QualifyingResult"]] = relationship(  # noqa: F821
        back_populates="race"
    )
    sprint_results: Mapped[list["SprintResult"]] = relationship(  # noqa: F821
        back_populates="race"
    )
    weather_data: Mapped[list["Weather"]] = relationship(back_populates="race")  # noqa: F821
    bets: Mapped[list["Bet"]] = relationship(back_populates="race")  # noqa: F821
    simulations: Mapped[list["Simulation"]] = relationship(  # noqa: F821
        back_populates="race"
    )
    predictions: Mapped[list["Prediction"]] = relationship(  # noqa: F821
        back_populates="race"
    )

    def __repr__(self) -> str:
        return f"<Race {self.race_name} R{self.round_number} ({self.season})>"
