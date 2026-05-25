"""SQLAlchemy ORM model — `plan_run.py`.

Parent table for every cashflow engine invocation. All per-run output rows
(`cashflow_annual_rows`, `cashflow_monthly_rows`, `cashflow_headline`,
`cashflow_fund_flow_summary`, `cashflow_plan_summary`) hang off this row and
cascade-delete with it. Mirrors `GoalPlanningOutput` envelope metadata.
"""


from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    ARRAY,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Text,
    func,
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.cashflow.enums import DetailLevel

if TYPE_CHECKING:
    from app.models.cashflow.annual_row import CashflowAnnualRow
    from app.models.cashflow.fund_flow_summary import CashflowFundFlowSummary
    from app.models.cashflow.headline import CashflowHeadline
    from app.models.cashflow.monthly_row import CashflowMonthlyRow
    from app.models.cashflow.plan_summary import CashflowPlanSummary
    from app.models.chat import ChatSession
    from app.models.user import User


class CashflowPlanRun(Base):
    __tablename__ = "cashflow_plan_runs"
    __table_args__ = (
        Index(
            "ix_cashflow_plan_runs_user_id_computed_at",
            "user_id",
            "computed_at",
            postgresql_ops={"computed_at": "DESC"},
        ),
        Index(
            "ix_cashflow_plan_runs_chat_session",
            "chat_session_id",
            postgresql_where=sa_text("chat_session_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    chat_session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    engine_version: Mapped[str] = mapped_column(Text, nullable=False)
    detail_level: Mapped[DetailLevel] = mapped_column(
        SAEnum(DetailLevel, name="detail_level_enum", create_constraint=True),
        nullable=False,
        default=DetailLevel.DEFAULT,
    )
    assumption_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    warnings: Mapped[List[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped[Optional["User"]] = relationship(back_populates="cashflow_plan_runs")
    chat_session: Mapped[Optional["ChatSession"]] = relationship()
    annual_rows: Mapped[List["CashflowAnnualRow"]] = relationship(
        back_populates="plan_run", cascade="all, delete-orphan"
    )
    monthly_rows: Mapped[List["CashflowMonthlyRow"]] = relationship(
        back_populates="plan_run", cascade="all, delete-orphan"
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
