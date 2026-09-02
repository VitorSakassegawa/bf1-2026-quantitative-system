"""ELO history model – tracks ELO changes over time."""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EntityType(str, enum.Enum):
    driver = "driver"
    team = "team"


class ELOHistory(Base):
    __tablename__ = "elo_history"

    __table_args__ = (
        # compute_elo deletes and reinserts history; without this a partial
        # failure leaves duplicate rows behind.
        UniqueConstraint(
            "entity_type", "entity_id", "race_id", name="uq_elo_history_entity_race"
        ),
        Index("ix_elo_history_race_id", "race_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    entity_type: Mapped[EntityType] = mapped_column(
        Enum(EntityType), nullable=False
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    race_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("races.id"), nullable=False
    )
    elo_before: Mapped[float] = mapped_column(Float, nullable=False)
    elo_after: Mapped[float] = mapped_column(Float, nullable=False)
    elo_delta: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return (
            f"<ELOHistory {self.entity_type.value} "
            f"{self.elo_before:.0f}→{self.elo_after:.0f} "
            f"(Δ{self.elo_delta:+.1f})>"
        )
