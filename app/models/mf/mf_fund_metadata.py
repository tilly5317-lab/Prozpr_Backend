"""SQLAlchemy ORM model — `mf_fund_metadata.py`.

Source-of-truth scheme catalogue populated from the upstream feed (mfapi.in /
AMFI). Holds only the static fields the source provides — identifiers, names,
plan/option type, and active flag. Internal ratings, fee schedules, sector
breakdowns, and other curated/dynamic data live alongside this row in
``mf_fund_rating`` (joined by ``scheme_code`` and/or ``isin``). Period returns
are not persisted here; they are derived from ``mf_nav_history``.
"""


from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional, TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

from app.models.mf.enums import MfOptionType, MfPlanType

if TYPE_CHECKING:
    from app.models.mf.mf_fund_rating import MfFundRating
    from app.models.mf.mf_nav_history import MfNavHistory


class MfFundMetadata(Base):
    """One row per AMFI scheme; static, source-fed fields only."""

    __tablename__ = "mf_fund_metadata"
    __table_args__ = (UniqueConstraint("scheme_code", name="uq_mf_fund_metadata_scheme_code"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scheme_code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    isin: Mapped[Optional[str]] = mapped_column(String(12), nullable=True, index=True)
    isin_div_reinvest: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
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

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    nav_rows: Mapped[List["MfNavHistory"]] = relationship(back_populates="fund_meta")
    rating: Mapped[Optional["MfFundRating"]] = relationship(
        back_populates="fund_meta",
        uselist=False,
        cascade="all, delete-orphan",
    )
