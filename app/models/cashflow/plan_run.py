"""Cashflow plan run header and 1:1 / 1:N output tables."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, List, Optional

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.cashflow.enums import DetailLevel, InvestmentSource

if TYPE_CHECKING:
    from app.models.chat import ChatSession
    from app.models.user import User


class CashflowPlanRun(Base):
    __tablename__ = "cashflow_plan_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    chat_session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    engine_version: Mapped[str] = mapped_column(Text, nullable=False)
    cause: Mapped[DetailLevel] = mapped_column(
        SAEnum(
            DetailLevel,
            name="detail_level_enum",
            values_callable=lambda e: [m.value for m in e],
            create_constraint=True,
        ),
        nullable=False,
        server_default=DetailLevel.default.value,
    )
    assumption_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    warnings: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped[Optional["User"]] = relationship(back_populates="cashflow_plan_runs")
    chat_session: Mapped[Optional["ChatSession"]] = relationship(
        back_populates="cashflow_plan_runs"
    )
    headline: Mapped[Optional["CashflowHeadline"]] = relationship(
        back_populates="plan_run", uselist=False, cascade="all, delete-orphan"
    )
    fund_flow_summary: Mapped[Optional["CashflowFundFlowSummary"]] = relationship(
        back_populates="plan_run", uselist=False, cascade="all, delete-orphan"
    )
    plan_summary: Mapped[Optional["CashflowPlanSummary"]] = relationship(
        back_populates="plan_run", uselist=False, cascade="all, delete-orphan"
    )
    annual_rows: Mapped[List["CashflowAnnualRow"]] = relationship(
        back_populates="plan_run", cascade="all, delete-orphan"
    )
    monthly_rows: Mapped[List["CashflowMonthlyRow"]] = relationship(
        back_populates="plan_run", cascade="all, delete-orphan"
    )


class CashflowAnnualRow(Base):
    __tablename__ = "cashflow_annual_rows"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    plan_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cashflow_plan_runs.id", ondelete="CASCADE"),
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


class CashflowMonthlyRow(Base):
    __tablename__ = "cashflow_monthly_rows"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    plan_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cashflow_plan_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
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
        SAEnum(
            InvestmentSource,
            name="investment_source_enum",
            values_callable=lambda e: [m.value for m in e],
            create_constraint=True,
        ),
        nullable=False,
        server_default=InvestmentSource.zero.value,
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


class CashflowHeadline(Base):
    __tablename__ = "cashflow_headline"

    plan_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cashflow_plan_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    years_to_last_goal: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    last_goal_date: Mapped[date] = mapped_column(Date, nullable=False)
    last_fy_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    number_of_goals: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    corpus_today: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    total_corpus_required_today: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    surplus_or_shortfall_today: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    corpus_closing: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    total_shortfall_fv: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    total_funded_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)

    plan_run: Mapped["CashflowPlanRun"] = relationship(back_populates="headline")


class CashflowFundFlowSummary(Base):
    __tablename__ = "cashflow_fund_flow_summary"

    plan_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cashflow_plan_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
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


class CashflowPlanSummary(Base):
    __tablename__ = "cashflow_plan_summary"

    plan_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cashflow_plan_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    top_line: Mapped[str] = mapped_column(Text, nullable=False)
    retirement_note: Mapped[str] = mapped_column(Text, nullable=False)
    cashflow_note: Mapped[str] = mapped_column(Text, nullable=False)
    goals: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    risks: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    next_steps: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    summary_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    plan_run: Mapped["CashflowPlanRun"] = relationship(back_populates="plan_summary")
