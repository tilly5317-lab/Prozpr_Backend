"""SQLAlchemy ORM model — `mf_nav_history.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.mf.mf_fund_metadata import MfFundMetadata


class MfNavHistory(Base):
    """Daily NAV feed; FK to metadata so scheme_code is validated."""

    __tablename__ = "mf_nav_history"
    __table_args__ = (UniqueConstraint("scheme_code", "nav_date", name="uq_mf_nav_scheme_date"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scheme_code: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("mf_fund_metadata.scheme_code", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    isin: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    scheme_name: Mapped[str] = mapped_column(String(200), nullable=False)
    mf_type: Mapped[str] = mapped_column(String(200), nullable=False)
    nav: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    nav_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    fund_meta: Mapped["MfFundMetadata"] = relationship(back_populates="nav_rows")
