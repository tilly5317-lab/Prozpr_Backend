"""User-scoped cashflow assumptions (1:1 per user)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Numeric, SmallInteger, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.domains.identity.models.user import User


class CashflowInputAssumptions(Base):
    """Mirrors ``GoalPlanningInput.assumptions`` — keyed by ``user_id``."""

    __tablename__ = "cashflow_input_assumptions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    general_inflation: Mapped[float] = mapped_column(Numeric(7, 6), nullable=False)
    inflation_property: Mapped[float] = mapped_column(Numeric(7, 6), nullable=False)
    inflation_child_abroad_education: Mapped[float] = mapped_column(
        Numeric(7, 6), nullable=False
    )
    inflation_child_local_education: Mapped[float] = mapped_column(
        Numeric(7, 6), nullable=False
    )
    inflation_child_marriage: Mapped[float] = mapped_column(Numeric(7, 6), nullable=False)
    inflation_household_expense: Mapped[float] = mapped_column(Numeric(7, 6), nullable=False)
    inflation_post_retirement: Mapped[float] = mapped_column(Numeric(7, 6), nullable=False)
    annual_income_growth: Mapped[float] = mapped_column(Numeric(7, 6), nullable=False)
    annual_invested_amount_growth: Mapped[float] = mapped_column(Numeric(7, 6), nullable=False)
    roi_near_term_post_tax: Mapped[float] = mapped_column(Numeric(7, 6), nullable=False)
    roi_mid_term_post_tax: Mapped[float] = mapped_column(Numeric(7, 6), nullable=False)
    roi_long_term_post_tax: Mapped[float] = mapped_column(Numeric(7, 6), nullable=False)
    roi_retired_portfolio_annual: Mapped[float] = mapped_column(Numeric(7, 6), nullable=False)
    near_term_horizon_years: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    medium_term_horizon_years: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    default_mortgage_tenure_years: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    default_mortgage_interest_annual: Mapped[float] = mapped_column(
        Numeric(7, 6), nullable=False
    )
    default_property_downpayment_pct: Mapped[float] = mapped_column(
        Numeric(7, 6), nullable=False
    )
    default_sip_share: Mapped[float] = mapped_column(Numeric(7, 6), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="cashflow_assumptions")
