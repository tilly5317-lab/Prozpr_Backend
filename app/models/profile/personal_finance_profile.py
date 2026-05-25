"""SQLAlchemy ORM model — `personal_finance_profile.py`.

Single user-financial profile row (1:1 with `users`). Holds both the original
onboarding-questionnaire fields (income/expense ranges, wealth sources,
personal values) and the cashflow-engine `ClientProfile` fields merged in from
`viewer_db_schema.md` (`annual_income`, `effective_tax_rate`,
`financial_assets`, `financial_liabilities_excl_mortgage`,
`monthly_household_expense`, `starting_monthly_investment`). New cashflow
fields are nullable so existing rows remain valid until they are populated via
onboarding or chat extraction.
"""


from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import User


class PersonalFinanceProfile(Base):
    __tablename__ = "personal_finance_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_personal_finance_profiles_user_id"),
        CheckConstraint(
            "annual_income IS NULL OR annual_income >= 0",
            name="ck_pfp_annual_income_nonneg",
        ),
        CheckConstraint(
            "effective_tax_rate IS NULL OR (effective_tax_rate >= 0 AND effective_tax_rate <= 1)",
            name="ck_pfp_effective_tax_rate_range",
        ),
        CheckConstraint(
            "financial_assets IS NULL OR financial_assets >= 0",
            name="ck_pfp_financial_assets_nonneg",
        ),
        CheckConstraint(
            "financial_liabilities_excl_mortgage IS NULL OR financial_liabilities_excl_mortgage >= 0",
            name="ck_pfp_financial_liabilities_nonneg",
        ),
        CheckConstraint(
            "monthly_household_expense IS NULL OR monthly_household_expense >= 0",
            name="ck_pfp_monthly_household_expense_nonneg",
        ),
        CheckConstraint(
            "starting_monthly_investment IS NULL OR starting_monthly_investment >= 0",
            name="ck_pfp_starting_monthly_investment_nonneg",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )

    # --- Original onboarding-questionnaire fields --------------------------
    selected_goals: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    custom_goals: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    investment_horizon: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    annual_income_min: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    annual_income_max: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    annual_expense_min: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    annual_expense_max: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    wealth_sources: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    personal_values: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    # --- Cashflow `ClientProfile` fields (merged from `user_financial`) ----
    annual_income: Mapped[Optional[float]] = mapped_column(Numeric(18, 2), nullable=True)
    effective_tax_rate: Mapped[Optional[float]] = mapped_column(Numeric(7, 6), nullable=True)
    financial_assets: Mapped[Optional[float]] = mapped_column(Numeric(18, 2), nullable=True)
    financial_liabilities_excl_mortgage: Mapped[Optional[float]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    monthly_household_expense: Mapped[Optional[float]] = mapped_column(
        Numeric(15, 2), nullable=True
    )
    starting_monthly_investment: Mapped[Optional[float]] = mapped_column(
        Numeric(15, 2), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="personal_finance_profile")
