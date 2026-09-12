"""One row per user: the summary of their net-worth series.

Exists so the hot read paths never aggregate over the series itself. Without it,
"does this user have history?" is a ``COUNT(*)`` and "how fresh is it?" is a
``MAX(recorded_date)`` — both of which the status endpoint ran on a 1.8-second poll.

It is also where the *provenance* and *quality* of the last rebuild live: which
statement produced it, whether that statement's ledger was complete, and how much of
the portfolio is sitting on a price we are not confident about. The chart can then say
"prices for 3 funds are provisional" instead of quietly showing a stale number as fact.

Written in the SAME transaction as the series (see ``services/networth/writer.py``), so
it can never describe a series that does not exist. Never CAS-scoped, for the same
reason ``user_portfolio_nav_history`` is not.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserNetworthSeriesState(Base):
    __tablename__ = "user_networth_series_state"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )

    first_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    last_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    row_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    built_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Which statement the last full rebuild read. Provenance only — never a scope key.
    built_from_cas_upload_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cas_uploads.id", ondelete="SET NULL"),
        nullable=True,
    )

    # False when the active statement covers only part of the user's history, so the
    # series had to be seeded from CAS opening balances rather than replayed from the
    # first ever purchase.
    ledger_complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    # Funds valued off a stated or stale NAV rather than a published one that day.
    degraded_schemes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    stale_priced_value: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, server_default="0"
    )

    # The opening-position adjustment the last rebuild had to apply to reconcile the
    # ledger with the authoritative holdings roll-up. ~0 for a complete ledger.
    anchor_value: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, server_default="0"
    )
    anchor_invested: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, server_default="0"
    )

    # Cost basis as of ``last_date``. The daily refresh carries this forward instead of
    # replaying the ledger — invested only moves when a transaction lands, and
    # transactions only arrive via a CAS upload, which triggers a full rebuild anyway.
    last_invested: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, server_default="0"
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
