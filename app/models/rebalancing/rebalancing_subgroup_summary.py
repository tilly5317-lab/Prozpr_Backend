"""SQLAlchemy ORM — per-asset_subgroup roll-up for a rebalancing run.

One row per ``(run_id, asset_subgroup)``: target vs current vs final holding,
plus rank counts. Mirrors ``Rebalancing.models.SubgroupSummary``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.rebalancing.rebalancing_run import RebalancingRun


class RebalancingSubgroupSummary(Base):
    __tablename__ = "rebalancing_subgroup_summaries"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "asset_subgroup",
            name="uq_rebalancing_subgroup_summaries_run_subgroup",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rebalancing_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    asset_subgroup: Mapped[str] = mapped_column(String(80), nullable=False)

    goal_target_inr: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    current_holding_inr: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    suggested_final_holding_inr: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    rebalance_inr: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    total_buy_inr: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    total_sell_inr: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)

    ranks_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ranks_with_holding: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ranks_with_action: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    run: Mapped["RebalancingRun"] = relationship(back_populates="subgroup_summaries")
