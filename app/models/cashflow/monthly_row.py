"""SQLAlchemy ORM model — `monthly_row.py`.

One row per month per cashflow plan run; persisted only when the run was
executed with `detail_level = 'full'`. Mirrors `MonthlyCashflowRow` in
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
    Enum as SAEnum,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.cashflow.enums import InvestmentSource

if TYPE_CHECKING:
    from app.models.cashflow.plan_run import CashflowPlanRun


class CashflowMonthlyRow(Base):
    __tablename__ = "cashflow_monthly_rows"
    __table_args__ = (
        UniqueConstraint("plan_run_id", "month_end_date", name="uq_monthly_run_month_end"),
        CheckConstraint(
            "month_end_date = (date_trunc('month', month_end_date) + INTERVAL '1 month - 1 day')::date",
            name="ck_month_end_date_is_eom",
        ),
        CheckConstraint("existing_mortgage_emi >= 0", name="ck_monthly_existing_emi_nonneg"),
        CheckConstraint("goal_mortgage_emi >= 0", name="ck_monthly_goal_emi_nonneg"),
        CheckConstraint("one_off_inflow >= 0", name="ck_monthly_one_off_in_nonneg"),
        CheckConstraint("one_off_outflow >= 0", name="ck_monthly_one_off_out_nonneg"),
        CheckConstraint("goal_payout >= 0", name="ck_monthly_goal_payout_nonneg"),
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
    month_end_date: Mapped[date] = mapped_column(Date, nullable=False)
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
    investment_source: Mapped[InvestmentSource] = mapped_column(
        SAEnum(InvestmentSource, name="investment_source_enum", create_constraint=False,
               values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        server_default=sa_text("'zero'"),
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

    plan_run: Mapped["CashflowPlanRun"] = relationship(back_populates="monthly_rows")
