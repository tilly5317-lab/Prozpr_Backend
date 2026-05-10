"""SQLAlchemy ORM — rebalancing engine runs and 1:1 totals roll-up.

One ``rebalancing_runs`` row per execution of the rebalancing engine
(``AI_Agents/src/Rebalancing``). Every run consumes a goal-allocation run as
its target — ``source_allocation_run_id`` is non-nullable.

Children:

- ``rebalancing_totals``              — 1:1 KPI roll-up (buy/sell/tax/exit-load).
- ``rebalancing_subgroup_summaries``  — per-asset-subgroup aggregate (target vs current vs final).
- ``rebalancing_fund_rows``           — per-fund full audit (one row per ``FundRowAfterStep5``).
- ``rebalancing_trades``              — execution-ready BUY/SELL/EXIT actions.
- ``rebalancing_warnings``            — engine warnings (codes + messages + affected ISINs).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, List, Optional

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.chat import ChatSession
    from app.models.goals.goal_allocation_run import GoalAllocationRun
    from app.models.portfolio import Portfolio
    from app.models.rebalancing.rebalancing_fund_row import RebalancingFundRow
    from app.models.rebalancing.rebalancing_subgroup_summary import (
        RebalancingSubgroupSummary,
    )
    from app.models.rebalancing.rebalancing_trade import RebalancingTrade
    from app.models.rebalancing.rebalancing_warning import RebalancingWarning
    from app.models.user import User


class RebalancingRunStatus(str, enum.Enum):
    """Lifecycle of a rebalancing recommendation."""

    pending = "pending"
    approved = "approved"
    executed = "executed"
    rejected = "rejected"


class TaxRegime(str, enum.Enum):
    """Indian tax regime selected for the run."""

    old = "old"
    new = "new"


class RebalancingRun(Base):
    """One execution of the rebalancing engine for a user's portfolio."""

    __tablename__ = "rebalancing_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chat_session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_allocation_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("goal_allocation_runs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    supersedes_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rebalancing_runs.id", ondelete="SET NULL"),
        nullable=True,
    )

    status: Mapped[RebalancingRunStatus] = mapped_column(
        SAEnum(
            RebalancingRunStatus,
            name="rebalancing_run_status_enum",
            create_constraint=True,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        default=RebalancingRunStatus.pending,
    )
    executed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    engine_request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(40), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    tax_regime: Mapped[TaxRegime] = mapped_column(
        SAEnum(
            TaxRegime,
            name="rebalancing_tax_regime_enum",
            create_constraint=True,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
    )
    effective_tax_rate_pct: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    total_corpus: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    rounding_step: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    stcg_offset_budget_inr: Mapped[Optional[float]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    carryforward_st_loss_inr: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    carryforward_lt_loss_inr: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )

    knob_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    request_input: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    used_cached_allocation: Mapped[Optional[bool]] = mapped_column(nullable=True)
    user_question: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="rebalancing_runs")
    portfolio: Mapped["Portfolio"] = relationship()
    chat_session: Mapped[Optional["ChatSession"]] = relationship()
    source_allocation_run: Mapped["GoalAllocationRun"] = relationship(
        back_populates="rebalancing_runs"
    )
    superseded_by: Mapped[Optional["RebalancingRun"]] = relationship(
        "RebalancingRun",
        remote_side="RebalancingRun.id",
        foreign_keys=[supersedes_id],
    )

    totals: Mapped[Optional["RebalancingTotals"]] = relationship(
        back_populates="run",
        uselist=False,
        cascade="all, delete-orphan",
    )
    subgroup_summaries: Mapped[List["RebalancingSubgroupSummary"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    fund_rows: Mapped[List["RebalancingFundRow"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    trades: Mapped[List["RebalancingTrade"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    warnings: Mapped[List["RebalancingWarning"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class RebalancingTotals(Base):
    """1:1 KPI roll-up for a rebalancing run."""

    __tablename__ = "rebalancing_totals"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rebalancing_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )

    total_buy_inr: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    total_sell_inr: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    net_cash_flow_inr: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    total_stcg_realised: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    total_ltcg_realised: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    total_stcg_net_off: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    total_tax_estimate_inr: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    total_exit_load_inr: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    unrebalanced_remainder_inr: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )

    rows_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    funds_to_buy_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    funds_to_sell_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    funds_to_exit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    funds_held_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    run: Mapped["RebalancingRun"] = relationship(back_populates="totals")
