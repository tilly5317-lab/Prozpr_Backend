"""SQLAlchemy ORM model — `portfolio_allocation_snapshot.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

from app.models.mf.enums import PortfolioSnapshotKind

if TYPE_CHECKING:
    from app.models.user import User


class PortfolioAllocationSnapshot(Base):
    """Ideal / suggested / actual allocation snapshots with full history."""

    __tablename__ = "portfolio_allocation_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    snapshot_kind: Mapped[PortfolioSnapshotKind] = mapped_column(
        SAEnum(PortfolioSnapshotKind, name="portfolio_snapshot_kind_enum", create_constraint=True),
        nullable=False,
    )
    allocation: Mapped[dict] = mapped_column(
        JSONB, nullable=False, comment="e.g. [{asset_class, weight_pct}, ...]"
    )
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    source: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="portfolio_allocation_snapshots")
