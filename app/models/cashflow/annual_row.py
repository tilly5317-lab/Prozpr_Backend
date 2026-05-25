"""SQLAlchemy ORM model — `annual_row.py`.

One row per Indian financial year (April-March, FY end March 31) per cashflow
plan run. Mirrors `AnnualCashflowRow` in
`AI_Agents/src/cashflow_statement/models.py`.
"""


from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.cashflow.plan_run import CashflowPlanRun


class CashflowAnnualRow(Base):
    __tablename__ = "cashflow_annual_rows"
    __table_args__ = (
        UniqueConstraint("plan_run_id", "fy_end_date", name="uq_annual_run_fy_end"),
        UniqueConstraint("plan_run_id", "fy_label", name="uq_annual_run_fy_label"),
        CheckConstraint("existing_mortgage_emi >= 0", name="ck_annual_existing_emi_nonneg"),
        CheckConstraint("goal_mortgage_emi >= 0", name="ck_annual_goal_emi_nonneg"),
        CheckConstraint("one_off_inflow >= 0", name="ck_annual_one_off_in_nonneg"),
        CheckConstraint("one_off_outflow >= 0", name="ck_annual_one_off_out_nonneg"),
        CheckConstraint("goal_payout >= 0", name="ck_annual_goal_payout_nonneg"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    plan_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cashflow_plan_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    fy_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    fy_label: Mapped[str] = mapped_column(String(8), nullable=False)

    income: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    income_tax: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    household_expense: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    savings_pre_emi: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    existing_mortgage_emi: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    goal_mortgage_emi: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    savings_post_emi: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    one_off_inflow: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, server_default="0"
    )
    one_off_outflow: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, server_default="0"
    )
    corpus_opening: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, server_default="0"
    )
    monthly_investment: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, server_default="0"
    )
    investment_returns: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, server_default="0"
    )
    goal_payout: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, server_default="0"
    )
    corpus_closing: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, server_default="0"
    )
    is_funded: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    plan_run: Mapped["CashflowPlanRun"] = relationship(back_populates="annual_rows")
