"""Asset-allocation aggregate table model."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Numeric, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.asset_allocation.run import AssetAllocationRun
    from app.models.user import User


class AssetClassSplitKind(str, enum.Enum):
    planned = "planned"
    actual = "actual"


class AssetAllocationAggregate(Base):
    __tablename__ = "asset_allocation_aggregate"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "split_kind",
            name="uq_asset_allocation_aggregate_run_kind",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("asset_allocation_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    split_kind: Mapped[AssetClassSplitKind] = mapped_column(
        SAEnum(
            AssetClassSplitKind,
            name="asset_class_split_kind_enum",
            create_constraint=True,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
    )

    equity_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    debt_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    others_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    equity_pct: Mapped[float] = mapped_column(Numeric(7, 2), nullable=False, default=0)
    debt_pct: Mapped[float] = mapped_column(Numeric(7, 2), nullable=False, default=0)
    others_pct: Mapped[float] = mapped_column(Numeric(7, 2), nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    run: Mapped["AssetAllocationRun"] = relationship(back_populates="aggregates")
    user: Mapped["User"] = relationship()
