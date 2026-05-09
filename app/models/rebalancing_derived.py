"""Normalized derived tables for rebalancing recommendation analytics."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.rebalancing import RebalancingRecommendation


class RebalancingRecommendationSummary(Base):
    """One summary row per recommendation; top-level metrics only."""

    __tablename__ = "rebalancing_recommendation_summaries"
    __table_args__ = (
        UniqueConstraint(
            "rebalancing_recommendation_id",
            name="uq_rebalancing_recommendation_summary_recommendation",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    rebalancing_recommendation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rebalancing_recommendations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    chat_session_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    user_question: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    spine_mode: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    grand_total: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    all_amounts_in_multiples_of_100: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    rebalancing_recommendation: Mapped["RebalancingRecommendation"] = relationship(
        back_populates="derived_summary"
    )


class RebalancingBucketRecommendation(Base):
    """Bucket-level recommendation: emergency/short/medium/long."""

    __tablename__ = "rebalancing_bucket_recommendations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    rebalancing_recommendation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rebalancing_recommendations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    bucket_name: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    total_goal_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    allocated_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    rebalancing_recommendation: Mapped["RebalancingRecommendation"] = relationship(
        back_populates="bucket_recommendations"
    )
    goals: Mapped[list["RebalancingBucketGoal"]] = relationship(
        back_populates="bucket_recommendation",
        cascade="all, delete-orphan",
    )
    subgroup_allocations: Mapped[list["RebalancingBucketSubgroupAllocation"]] = relationship(
        back_populates="bucket_recommendation",
        cascade="all, delete-orphan",
    )


class RebalancingBucketGoal(Base):
    """Goal-to-bucket assignment rows, one row per goal in a bucket."""

    __tablename__ = "rebalancing_bucket_goals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    bucket_recommendation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rebalancing_bucket_recommendations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    goal_name: Mapped[str] = mapped_column(String(150), nullable=False)
    time_to_goal_months: Mapped[Optional[int]] = mapped_column(nullable=True)
    amount_needed: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    goal_priority: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    investment_goal: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    goal_rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    bucket_recommendation: Mapped["RebalancingBucketRecommendation"] = relationship(
        back_populates="goals"
    )


class RebalancingBucketSubgroupAllocation(Base):
    """Subgroup split inside each bucket."""

    __tablename__ = "rebalancing_bucket_subgroup_allocations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    bucket_recommendation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rebalancing_bucket_recommendations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subgroup: Mapped[str] = mapped_column(String(80), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    pct_of_bucket: Mapped[Optional[float]] = mapped_column(Numeric(7, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    bucket_recommendation: Mapped["RebalancingBucketRecommendation"] = relationship(
        back_populates="subgroup_allocations"
    )


class RebalancingAssetClassBreakdown(Base):
    """Planned vs actual asset-class totals per bucket and at total level."""

    __tablename__ = "rebalancing_asset_class_breakdowns"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    rebalancing_recommendation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rebalancing_recommendations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    breakdown_kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="bucket")
    bucket_name: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    equity_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    debt_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    others_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    equity_pct: Mapped[Optional[float]] = mapped_column(Numeric(7, 2), nullable=True)
    debt_pct: Mapped[Optional[float]] = mapped_column(Numeric(7, 2), nullable=True)
    others_pct: Mapped[Optional[float]] = mapped_column(Numeric(7, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    rebalancing_recommendation: Mapped["RebalancingRecommendation"] = relationship(
        back_populates="asset_class_breakdowns"
    )


class RebalancingFutureInvestment(Base):
    """Gap/top-up recommendation rows, usually per bucket."""

    __tablename__ = "rebalancing_future_investments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    rebalancing_recommendation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rebalancing_recommendations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    bucket_name: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    future_investment_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    rebalancing_recommendation: Mapped["RebalancingRecommendation"] = relationship(
        back_populates="future_investments"
    )
