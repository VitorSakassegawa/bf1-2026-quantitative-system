"""Bet model – user token allocations for races."""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class StrategyType(str, enum.Enum):
    conservative = "conservative"
    balanced = "balanced"
    aggressive = "aggressive"
    ultra_aggressive = "ultra_aggressive"


class BetStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    scored = "scored"
    void = "void"


class Bet(Base):
    __tablename__ = "bets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    race_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("races.id"), nullable=False
    )
    strategy_type: Mapped[StrategyType] = mapped_column(
        Enum(StrategyType), default=StrategyType.balanced, nullable=False
    )
    total_tokens: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    allocations: Mapped[dict] = mapped_column(JSONB, nullable=False)
    expected_value: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    actual_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[BetStatus] = mapped_column(
        Enum(BetStatus), default=BetStatus.pending, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    user: Mapped["User"] = relationship(back_populates="bets")  # noqa: F821
    race: Mapped["Race"] = relationship(back_populates="bets")  # noqa: F821

    def __repr__(self) -> str:
        return f"<Bet {self.strategy_type.value} tokens={self.total_tokens} EV={self.expected_value:.2f}>"
