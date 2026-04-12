"""SQLAlchemy ORM model — `mf_fund_metadata.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
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

from app.models.mf.enums import MfOptionType, MfPlanType


class MfFundMetadata(Base):
    """One row per AMFI scheme; join key is scheme_code."""

    __tablename__ = "mf_fund_metadata"
    __table_args__ = (UniqueConstraint("scheme_code", name="uq_mf_fund_metadata_scheme_code"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scheme_code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    scheme_name: Mapped[str] = mapped_column(String(200), nullable=False)
    amc_name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    sub_category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    plan_type: Mapped[MfPlanType] = mapped_column(
        SAEnum(MfPlanType, name="mf_plan_type_enum", create_constraint=True), nullable=False
    )
    option_type: Mapped[MfOptionType] = mapped_column(
        SAEnum(MfOptionType, name="mf_option_type_enum", create_constraint=True), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    risk_rating_sebi: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    asset_class_sebi: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    asset_class: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
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
    returns_1y_pct: Mapped[Optional[float]] = mapped_column(Numeric(8, 4), nullable=True)
    returns_3y_pct: Mapped[Optional[float]] = mapped_column(Numeric(8, 4), nullable=True)
    returns_5y_pct: Mapped[Optional[float]] = mapped_column(Numeric(8, 4), nullable=True)
    returns_10y_pct: Mapped[Optional[float]] = mapped_column(Numeric(8, 4), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    nav_rows: Mapped[List["MfNavHistory"]] = relationship(back_populates="fund_meta")
