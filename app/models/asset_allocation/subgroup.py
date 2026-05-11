"""Asset-allocation bucket subgroup table model."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.asset_allocation.bucket import AssetAllocationBucket
    from app.models.user import User


class AssetAllocationBucketSubgroup(Base):
    __tablename__ = "asset_allocation_bucket_subgroups"
    __table_args__ = (
        UniqueConstraint(
            "bucket_id",
            "subgroup",
            name="uq_asset_allocation_bucket_subgroups_bucket_subgroup",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    bucket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("asset_allocation_buckets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subgroup: Mapped[str] = mapped_column(String(80), nullable=False)

    planned_amount: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    actual_amount: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    planned_pct_of_bucket: Mapped[Optional[float]] = mapped_column(
        Numeric(7, 2), nullable=True
    )
    actual_pct_of_bucket: Mapped[Optional[float]] = mapped_column(
        Numeric(7, 2), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    bucket: Mapped["AssetAllocationBucket"] = relationship(back_populates="subgroups")
    user: Mapped["User"] = relationship()
