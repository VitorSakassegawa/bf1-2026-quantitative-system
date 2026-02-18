"""Team model – F1 constructor / team data and KPIs."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    current_elo: Mapped[float] = mapped_column(Float, default=1500.0, nullable=False)
    reliability_index: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_pit_stop_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    strategic_error_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    drivers: Mapped[list["Driver"]] = relationship(back_populates="team")  # noqa: F821

    def __repr__(self) -> str:
        return f"<Team {self.name} (ELO: {self.current_elo:.0f})>"
