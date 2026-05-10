"""SQLAlchemy ORM model — `mf_fund_rating.py`.

Curated (internal) data layer for each scheme: SEBI / external / our own
ratings, portfolio-manager attributions, fee schedule, exit-load terms, and
asset-mix percentages. Linked one-to-one to ``mf_fund_metadata`` by
``scheme_code``; ``isin`` is mirrored here for cross-source joins. Period
returns are deliberately absent — they are computed from
``mf_nav_history`` on demand.
"""


from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional, TYPE_CHECKING

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.mf.mf_fund_metadata import MfFundMetadata


class MfFundRating(Base):
    """Internal rating + dynamic facts for a scheme (1:1 with mf_fund_metadata)."""

    __tablename__ = "mf_fund_ratings"
    __table_args__ = (UniqueConstraint("scheme_code", name="uq_mf_fund_ratings_scheme_code"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scheme_code: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("mf_fund_metadata.scheme_code", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    isin: Mapped[Optional[str]] = mapped_column(String(12), nullable=True, index=True)

    risk_rating_sebi: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    asset_class_sebi: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    asset_class: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    asset_subgroup: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    portfolio_managers_current: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    portfolio_managers_history: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    portfolio_manager_change_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    rating_external_agency_1: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    rating_external_agency_2: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    our_rating_parameter_1: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    our_rating_parameter_2: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    our_rating_parameter_3: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    our_rating_history_parameter_1: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    our_rating_history_parameter_2: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    our_rating_history_parameter_3: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    direct_plan_fees: Mapped[Optional[float]] = mapped_column(Numeric(6, 4), nullable=True)
    regular_plan_fees: Mapped[Optional[float]] = mapped_column(Numeric(6, 4), nullable=True)
    entry_load_percent: Mapped[Optional[float]] = mapped_column(Numeric(6, 4), nullable=True)
    exit_load_percent: Mapped[Optional[float]] = mapped_column(Numeric(6, 4), nullable=True)
    exit_load_months: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    large_cap_equity_pct: Mapped[Optional[float]] = mapped_column(Numeric(6, 2), nullable=True)
    mid_cap_equity_pct: Mapped[Optional[float]] = mapped_column(Numeric(6, 2), nullable=True)
    small_cap_equity_pct: Mapped[Optional[float]] = mapped_column(Numeric(6, 2), nullable=True)
    debt_pct: Mapped[Optional[float]] = mapped_column(Numeric(6, 2), nullable=True)
    others_pct: Mapped[Optional[float]] = mapped_column(Numeric(6, 2), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    fund_meta: Mapped["MfFundMetadata"] = relationship(back_populates="rating")
