"""SQLAlchemy ORM — bucket-level outputs for an asset-allocation run.

Postgres: ``asset_allocation_buckets`` and children. See ``TABLES.md``.
"""

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
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.asset_allocation.run import AssetAllocationRun, AssetAllocationRunTarget


class AllocationBucketName(str, enum.Enum):
    """Time-horizon bucket names mirrored from the pipeline."""

    emergency = "emergency"
    short_term = "short_term"
    medium_term = "medium_term"
    long_term = "long_term"


class AssetClassSplitKind(str, enum.Enum):
    """Planned (pre-guardrail) vs actual (post-guardrail) split."""

    planned = "planned"
    actual = "actual"


class AssetAllocationBucket(Base):
    """Bucket-level allocation row (one of the four time horizons)."""

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
    bucket_run_targets: Mapped[List["AssetAllocationBucketRunTarget"]] = relationship(
        back_populates="bucket", cascade="all, delete-orphan"
    )
    subgroups: Mapped[List["AssetAllocationBucketSubgroup"]] = relationship(
        back_populates="bucket", cascade="all, delete-orphan"
    )
    asset_classes: Mapped[List["AssetAllocationBucketAssetClass"]] = relationship(
        back_populates="bucket", cascade="all, delete-orphan"
    )


class AssetAllocationBucketRunTarget(Base):
    """M:N join — which per-run targets were assigned to which bucket."""

    __tablename__ = "asset_allocation_bucket_run_targets"
    __table_args__ = (
        UniqueConstraint(
            "bucket_id",
            "run_target_id",
            name="uq_asset_allocation_bucket_run_targets_bucket_target",
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
    run_target_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("asset_allocation_run_targets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    goal_rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    bucket: Mapped["AssetAllocationBucket"] = relationship(
        back_populates="bucket_run_targets"
    )
    run_target: Mapped["AssetAllocationRunTarget"] = relationship()


class AssetAllocationBucketSubgroup(Base):
    """Subgroup amount inside a bucket.

    ``user_id`` is denormalized from the parent run so subgroup queries
    need no join through buckets → runs.
    """

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
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
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


class AssetAllocationBucketAssetClass(Base):
    """Equity / debt / others split for a bucket — one row per kind (planned, actual)."""

    __tablename__ = "asset_allocation_bucket_asset_classes"
    __table_args__ = (
        UniqueConstraint(
            "bucket_id",
            "split_kind",
            name="uq_asset_allocation_bucket_asset_classes_bucket_kind",
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

    bucket: Mapped["AssetAllocationBucket"] = relationship(back_populates="asset_classes")


class AssetAllocationAggregate(Base):
    """Run-level equity / debt / others roll-up — one ``planned`` + one ``actual`` row per run.

    Replaces the per-run totals that previously lived directly on the run row.
    ``user_id`` is denormalized for query convenience.
    """

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
            create_type=False,
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
