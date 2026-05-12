"""SQLAlchemy ORM — per-fund audit row for a rebalancing run.

One row per ``Rebalancing.models.FundRowAfterStep5`` produced by the engine.
Holds the full audit trail through the 5 engine steps so analytics, customer
cards, and post-mortem queries can read it back without re-running the engine.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
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


class RebalancingFundRow(Base):
    __tablename__ = "rebalancing_fund_rows"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "isin",
            "rank",
            name="uq_rebalancing_fund_rows_run_isin_rank",
        ),
        Index(
            "ix_rebalancing_fund_rows_run_subgroup",
            "run_id",
            "asset_subgroup",
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

    isin: Mapped[str] = mapped_column(String(20), nullable=False)
    recommended_fund: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_subgroup: Mapped[str] = mapped_column(String(80), nullable=False)
    sub_category: Mapped[str] = mapped_column(String(80), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    fund_rating: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    is_recommended: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    target_amount_pre_cap: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    max_pct: Mapped[float] = mapped_column(Numeric(7, 4), nullable=False, default=0)
    target_pre_cap_pct: Mapped[float] = mapped_column(
        Numeric(7, 4), nullable=False, default=0
    )
    target_own_capped_pct: Mapped[float] = mapped_column(
        Numeric(7, 4), nullable=False, default=0
    )
    final_target_pct: Mapped[float] = mapped_column(
        Numeric(7, 4), nullable=False, default=0
    )
    final_target_amount: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )

    present_allocation_inr: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    invested_cost_inr: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    st_value_inr: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    st_cost_inr: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    lt_value_inr: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    lt_cost_inr: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)

    exit_load_pct: Mapped[float] = mapped_column(Numeric(7, 4), nullable=False, default=0)
    exit_load_months: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    units_within_exit_load_period: Mapped[float] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    current_nav: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    exit_load_amount: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )

    diff: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    exit_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    worth_to_change: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stcg_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    ltcg_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)

    pass1_buy_amount: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    pass1_underbuy_amount: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    pass1_sell_amount: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    pass1_undersell_amount: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    pass1_sell_lt_amount: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    pass1_realised_ltcg: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    pass1_sell_st_amount: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    pass1_realised_stcg: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    stcg_budget_remaining_after_pass1: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    pass1_sell_amount_no_stcg_cap: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    pass1_undersell_due_to_stcg_cap: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    pass1_blocked_stcg_value: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    holding_after_initial_trades: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )

    stcg_offset_amount: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    pass2_sell_amount: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    pass2_undersell_amount: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    final_holding_amount: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    run: Mapped["RebalancingRun"] = relationship(back_populates="fund_rows")
