"""SQLAlchemy ORM model — `financial_goal.py`.

Single canonical `goals` table covering both the legacy onboarding goal
representation (kept for backwards compatibility with portfolio /
asset-allocation services) and the cashflow-engine goal taxonomy specified in
`viewer_db_schema.md`. All new cashflow-specific columns are nullable; the
engine writes the cashflow shape (`goal_type_cashflow`, `goal_date`, the
`*_pv` / `*_fv` pair, mortgage fields), while existing onboarding callers
continue to read `goal_name`, `present_value_amount`, `target_date`.

Conditional `CHECK` constraints enforce per-type field presence only when the
new `goal_type_cashflow` column is set, so legacy rows with `goal_type_cashflow
IS NULL` are unaffected.
"""


from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

from app.models.cashflow.enums import GoalTypeCashflow
from app.models.goals.enums import GoalPriority, GoalStatus


class FinancialGoal(Base):
    __tablename__ = "goals"
    __table_args__ = (
        # --- Legacy onboarding-shape constraints (unchanged) ---------------
        CheckConstraint(
            "present_value_amount IS NULL OR present_value_amount > 0",
            name="ck_goals_present_value_positive",
        ),
        CheckConstraint(
            "inflation_rate IS NULL OR (inflation_rate >= 0 AND inflation_rate <= 50)",
            name="ck_goals_inflation_range",
        ),
        # --- New cashflow-shape constraints (only enforced when typed) -----
        CheckConstraint(
            "goal_type_cashflow IS NULL "
            "OR goal_type_cashflow IN ('retirement', 'property') "
            "OR goal_value_pv IS NOT NULL OR goal_value_fv IS NOT NULL",
            name="ck_goal_value_present",
        ),
        CheckConstraint(
            "goal_type_cashflow IS NULL OR goal_type_cashflow <> 'property' "
            "OR target_pv IS NOT NULL OR target_fv IS NOT NULL",
            name="ck_property_target_present",
        ),
        CheckConstraint(
            "goal_type_cashflow IS NULL OR goal_type_cashflow <> 'retirement' "
            "OR (date_of_birth IS NOT NULL)",
            name="ck_retirement_fields",
        ),
        CheckConstraint(
            "downpayment_pct IS NULL OR (downpayment_pct >= 0 AND downpayment_pct <= 1)",
            name="ck_goals_downpayment_pct_range",
        ),
        CheckConstraint(
            "upfront_amount IS NULL OR upfront_amount >= 0",
            name="ck_goals_upfront_amount_nonneg",
        ),
        Index(
            "uq_goals_name_per_user",
            "user_id",
            func.lower(sa_text("name")),
            unique=True,
            postgresql_where=sa_text("name IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    # --- Legacy onboarding columns (kept nullable for back-compat) --------
    goal_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    present_value_amount: Mapped[Optional[float]] = mapped_column(
        Numeric(15, 2), nullable=True
    )
    inflation_rate: Mapped[Optional[float]] = mapped_column(
        Numeric(5, 2), nullable=True, server_default="6.00"
    )
    target_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    priority: Mapped[GoalPriority] = mapped_column(
        SAEnum(GoalPriority, name="goal_priority_enum_v2", create_constraint=True),
        nullable=False,
        default=GoalPriority.HIGH,
    )
    status: Mapped[GoalStatus] = mapped_column(
        SAEnum(GoalStatus, name="goal_status_enum_v2", create_constraint=True),
        nullable=False,
        default=GoalStatus.ACTIVE,
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- Asset-allocation pipeline fields (formerly the per-run snapshot
    # table ``goal_allocation_goals``; now folded into the canonical goal
    # row). ---
    time_to_goal_months: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    amount_needed: Mapped[Optional[float]] = mapped_column(Numeric(18, 2), nullable=True)
    goal_priority: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    investment_goal: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)

    # --- Cashflow-engine columns (from viewer_db_schema.md §5.4) ----------
    name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    goal_type_cashflow: Mapped[Optional[GoalTypeCashflow]] = mapped_column(
        SAEnum(
            GoalTypeCashflow,
            name="goal_type_enum",
            create_constraint=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=True,
    )
    goal_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # custom / education / marriage
    goal_value_pv: Mapped[Optional[float]] = mapped_column(Numeric(18, 2), nullable=True)
    goal_value_fv: Mapped[Optional[float]] = mapped_column(Numeric(18, 2), nullable=True)
    # property
    target_pv: Mapped[Optional[float]] = mapped_column(Numeric(18, 2), nullable=True)
    target_fv: Mapped[Optional[float]] = mapped_column(Numeric(18, 2), nullable=True)
    is_downpayment_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    upfront_amount: Mapped[Optional[float]] = mapped_column(Numeric(18, 2), nullable=True)
    downpayment_pct: Mapped[Optional[float]] = mapped_column(Numeric(7, 6), nullable=True)
    inflation_annual: Mapped[Optional[float]] = mapped_column(Numeric(7, 6), nullable=True)
    mortgage_tenure_years: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    mortgage_interest_annual: Mapped[Optional[float]] = mapped_column(
        Numeric(7, 6), nullable=True
    )
    # retirement
    date_of_birth: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user = relationship("User", back_populates="financial_goals")
    contributions: Mapped[List["GoalContribution"]] = relationship(
        back_populates="goal", cascade="all, delete-orphan"
    )
    holdings: Mapped[List["GoalHolding"]] = relationship(
        back_populates="goal", cascade="all, delete-orphan"
    )
