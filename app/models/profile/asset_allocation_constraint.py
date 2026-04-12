"""SQLAlchemy ORM model — `asset_allocation_constraint.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.profile.investment_constraint import InvestmentConstraint


class AssetAllocationConstraint(Base):
    __tablename__ = "asset_allocation_constraints"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    constraint_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investment_constraints.id", ondelete="CASCADE")
    )

    asset_class: Mapped[str] = mapped_column(String(100), nullable=False)
    min_allocation: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), nullable=True)
    max_allocation: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    investment_constraint: Mapped["InvestmentConstraint"] = relationship(
        back_populates="allocation_constraints"
    )
