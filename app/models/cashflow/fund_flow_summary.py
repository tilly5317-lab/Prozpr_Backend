"""SQLAlchemy ORM model — `fund_flow_summary.py`.

Per-run horizon-bridge totals plus the present-value goal-funding snapshot
(1:1 with `cashflow_plan_runs`). Mirrors `FundFlowSummary` in
`AI_Agents/src/cashflow_statement/models.py`.
"""


from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.cashflow.plan_run import CashflowPlanRun


class CashflowFundFlowSummary(Base):
    __tablename__ = "cashflow_fund_flow_summary"
    __table_args__ = (
        CheckConstraint("total_one_off_in >= 0", name="ck_fund_flow_one_off_in_nonneg"),
        CheckConstraint("total_one_off_out >= 0", name="ck_fund_flow_one_off_out_nonneg"),
        CheckConstraint("total_goals_paid >= 0", name="ck_fund_flow_goals_paid_nonneg"),
    )

    plan_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cashflow_plan_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    corpus_opening: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    total_investments: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    total_roi: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    total_one_off_in: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    total_one_off_out: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    total_goals_paid: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    corpus_closing: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    corpus_today: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    total_corpus_required_today: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    surplus_or_shortfall_today: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)

    plan_run: Mapped["CashflowPlanRun"] = relationship(back_populates="fund_flow_summary")
