"""SQLAlchemy ORM — bucket-level outputs of a goal-based allocation run.

Each ``goal_allocation_runs`` row has up to four ``goal_allocation_buckets``
children (emergency / short_term / medium_term / long_term). Each bucket has:

- ``goal_allocation_bucket_goals``         — which goals were placed in it
                                              (M:N join to ``goal_allocation_goals``).
- ``goal_allocation_bucket_subgroups``     — subgroup amounts inside the bucket
                                              (e.g. ``low_beta_equities``,
                                              ``debt_subgroup``, ``gold``).
- ``goal_allocation_bucket_asset_classes`` — equity / debt / others split for
                                              the bucket, planned + actual.
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
    from app.models.goals.goal_allocation_run import GoalAllocationGoal, GoalAllocationRun


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


class GoalAllocationBucket(Base):
    """Bucket-level allocation row (one of the four time horizons)."""

    __tablename__ = "goal_allocation_buckets"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "bucket_name", name="uq_goal_allocation_buckets_run_bucket"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("goal_allocation_runs.id", ondelete="CASCADE"),
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

    run: Mapped["GoalAllocationRun"] = relationship(back_populates="buckets")
    bucket_goals: Mapped[List["GoalAllocationBucketGoal"]] = relationship(
        back_populates="bucket", cascade="all, delete-orphan"
    )
    subgroups: Mapped[List["GoalAllocationBucketSubgroup"]] = relationship(
        back_populates="bucket", cascade="all, delete-orphan"
    )
    asset_classes: Mapped[List["GoalAllocationBucketAssetClass"]] = relationship(
        back_populates="bucket", cascade="all, delete-orphan"
    )


class GoalAllocationBucketGoal(Base):
    """M:N join — which goals went into which bucket, with per-pair rationale."""

    __tablename__ = "goal_allocation_bucket_goals"
    __table_args__ = (
        UniqueConstraint(
            "bucket_id",
            "goal_id",
            name="uq_goal_allocation_bucket_goals_bucket_goal",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    bucket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("goal_allocation_buckets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    goal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("goal_allocation_goals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    goal_rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    bucket: Mapped["GoalAllocationBucket"] = relationship(back_populates="bucket_goals")
    goal: Mapped["GoalAllocationGoal"] = relationship()


class GoalAllocationBucketSubgroup(Base):
    """Subgroup amount inside a bucket.

    Example: bucket=long_term, subgroup=low_beta_equities,
    actual_amount=180000, actual_pct_of_bucket=30.0.

    Holds both planned (pre-guardrail) and actual (post-guardrail) figures
    so the planned-vs-actual diff is queryable without joining tables.
    """

    __tablename__ = "goal_allocation_bucket_subgroups"
    __table_args__ = (
        UniqueConstraint(
            "bucket_id",
            "subgroup",
            name="uq_goal_allocation_bucket_subgroups_bucket_subgroup",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    bucket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("goal_allocation_buckets.id", ondelete="CASCADE"),
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

    bucket: Mapped["GoalAllocationBucket"] = relationship(back_populates="subgroups")


class GoalAllocationBucketAssetClass(Base):
    """Equity / debt / others split for a bucket — one row per kind (planned, actual)."""

    __tablename__ = "goal_allocation_bucket_asset_classes"
    __table_args__ = (
        UniqueConstraint(
            "bucket_id",
            "split_kind",
            name="uq_goal_allocation_bucket_asset_classes_bucket_kind",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    bucket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("goal_allocation_buckets.id", ondelete="CASCADE"),
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

    bucket: Mapped["GoalAllocationBucket"] = relationship(back_populates="asset_classes")
