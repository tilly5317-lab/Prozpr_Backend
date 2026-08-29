"""Tracks a user's one-time net-worth-history backfill job.

A row records the lifecycle of the (potentially slow) job that fetches each
fund's NAV history back to its first transaction date and then computes the
daily portfolio net-worth series into ``user_portfolio_nav_history``. The
frontend polls the latest row for this user to render a % completion bar.

Status: ``pending`` -> ``running`` -> ``success`` | ``failed``.
Phase:  ``queued`` -> ``fetching_nav`` -> ``computing`` -> ``done``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.domains.ingestion.models.cas_upload import CasScoped


class PortfolioNetworthJob(CasScoped, Base):
    __tablename__ = "portfolio_networth_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="pending"
    )
    phase: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="queued"
    )
    progress_pct: Mapped[float] = mapped_column(
        Numeric(5, 2), nullable=False, server_default="0"
    )
    message: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    # The earliest day the computed series covers (first transaction date).
    history_from: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    days_total: Mapped[Optional[int]] = mapped_column(Numeric(8, 0), nullable=True)

    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
