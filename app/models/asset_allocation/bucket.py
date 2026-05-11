"""Asset-allocation bucket table model."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.asset_allocation.run import AssetAllocationRun
    from app.models.asset_allocation.subgroup import AssetAllocationBucketSubgroup


class AllocationBucketName(str, enum.Enum):
    emergency = "emergency"
    short_term = "short_term"
    medium_term = "medium_term"
    long_term = "long_term"


class AssetAllocationBucket(Base):
    __tablename__ = "asset_allocation_buckets"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "bucket_name", name="uq_asset_allocation_buckets_run_bucket"
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
    bucket_name: Mapped[AllocationBucketName] = mapped_column(
        SAEnum(
            AllocationBucketName,
            name="allocation_bucket_name_enum",
            create_constraint=True,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
    )

    total_goal_amount: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    allocated_amount: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    future_investment_amount: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    future_investment_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    run: Mapped["AssetAllocationRun"] = relationship(back_populates="buckets")
    subgroups: Mapped[List["AssetAllocationBucketSubgroup"]] = relationship(
        back_populates="bucket", cascade="all, delete-orphan"
    )
