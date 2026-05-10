"""SQLAlchemy ORM — goal-based allocation pipeline runs and goal snapshots.

One ``goal_allocation_runs`` row per execution of the goal-based allocation
pipeline (``AI_Agents/src/asset_allocation_pydantic``). Children:

- ``goal_allocation_goals``      — goals frozen at run time (1:N).
- ``goal_allocation_buckets``    — bucket-level outputs (1:N, see goal_allocation_bucket.py).

A run may be re-issued (e.g. user nudges risk score); ``supersedes_id`` chains
runs without overwriting history.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.chat import ChatSession
    from app.models.goals.financial_goal import FinancialGoal
    from app.models.goals.goal_allocation_bucket import GoalAllocationBucket
    from app.models.portfolio import Portfolio
    from app.models.rebalancing.rebalancing_run import RebalancingRun
    from app.models.user import User


class GoalAllocationRunStatus(str, enum.Enum):
    """Lifecycle of a goal-based allocation recommendation."""

    pending = "pending"
    approved = "approved"
    superseded = "superseded"
    rejected = "rejected"


class GoalAllocationRun(Base):
    """One execution of the goal-based allocation pipeline."""

    __tablename__ = "goal_allocation_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    portfolio_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("portfolios.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    chat_session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    supersedes_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("goal_allocation_runs.id", ondelete="SET NULL"),
        nullable=True,
    )

    status: Mapped[GoalAllocationRunStatus] = mapped_column(
        SAEnum(
            GoalAllocationRunStatus,
            name="goal_allocation_run_status_enum",
            create_constraint=True,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        default=GoalAllocationRunStatus.pending,
    )

    pipeline_source: Mapped[str] = mapped_column(
        String(80), nullable=False, default="asset_allocation_pydantic"
    )
    spine_mode: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    user_question: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    input_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    client_age: Mapped[int] = mapped_column(Integer, nullable=False)
    client_occupation: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    client_effective_risk_score: Mapped[float] = mapped_column(
        Numeric(5, 2), nullable=False
    )
    total_corpus: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)

    grand_total: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    equity_total: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    debt_total: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    others_total: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    equity_total_pct: Mapped[float] = mapped_column(Numeric(7, 2), nullable=False, default=0)
    debt_total_pct: Mapped[float] = mapped_column(Numeric(7, 2), nullable=False, default=0)
    others_total_pct: Mapped[float] = mapped_column(Numeric(7, 2), nullable=False, default=0)

    all_amounts_in_multiples_of_100: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="goal_allocation_runs")
    portfolio: Mapped[Optional["Portfolio"]] = relationship()
    chat_session: Mapped[Optional["ChatSession"]] = relationship()
    superseded_by: Mapped[Optional["GoalAllocationRun"]] = relationship(
        "GoalAllocationRun",
        remote_side="GoalAllocationRun.id",
        foreign_keys=[supersedes_id],
    )

    goals: Mapped[List["GoalAllocationGoal"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    buckets: Mapped[List["GoalAllocationBucket"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    rebalancing_runs: Mapped[List["RebalancingRun"]] = relationship(
        back_populates="source_allocation_run"
    )


class GoalAllocationGoal(Base):
    """Goal frozen at allocation run time.

    Captures the goal as the pipeline saw it, even if the canonical user goal
    (``goals.id``) is later edited or deleted. ``financial_goal_id`` is
    nullable because chat-driven runs may include ad-hoc goals not stored in
    the canonical table.
    """

    __tablename__ = "goal_allocation_goals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("goal_allocation_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    financial_goal_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("goals.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    goal_name: Mapped[str] = mapped_column(String(150), nullable=False)
    time_to_goal_months: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_needed: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    goal_priority: Mapped[str] = mapped_column(String(40), nullable=False)
    investment_goal: Mapped[str] = mapped_column(
        String(60), nullable=False, default="wealth_creation"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    run: Mapped["GoalAllocationRun"] = relationship(back_populates="goals")
    financial_goal: Mapped[Optional["FinancialGoal"]] = relationship()
