"""Materialised end-state of every (user, scheme) position.

This is what lets the daily net-worth refresh be ONE set-based statement for the whole
fleet instead of a per-user Python loop (``services/networth/daily.py``).

**It is also a scoping firewall.** ``mf_transactions`` is ``CasScoped``, and the CAS read
hook only filters ORM ``SELECT``s — so any fleet-wide raw-SQL aggregate over the ledger
would silently double-count every user who has ever uploaded twice. These rows are
written once, by the rebuild, *inside* the user's CAS scope; the daily job then reads
them with plain SQL and cannot reintroduce that bug.

``nav_key`` is the identifier that actually prices the holding in ``mf_nav_history``.
CAS ingest stores whichever identifier it could resolve in ``scheme_code`` — sometimes
an AMFI code, sometimes an ISIN — and matching on ``upper(isin)`` in the hot loop is
unindexable. Resolving it once, here, keeps the pricing join on an index.

The opening columns exist because a CAS covering only part of the user's history starts
each scheme at a non-zero balance; ``_derive_scheme_snapshot`` already reads it and
already knows whether the transaction ledger reproduces the statement's closing balance
(``ledger_agrees``). Persisting both is what lets the rebuild seed the replay correctly
instead of papering over the gap with one flat constant.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserSchemePosition(Base):
    __tablename__ = "user_scheme_position"
    __table_args__ = (Index("ix_user_scheme_position_nav_key", "nav_key"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    scheme_code: Mapped[str] = mapped_column(String(20), primary_key=True)

    units: Mapped[float] = mapped_column(
        Numeric(18, 6), nullable=False, server_default="0"
    )
    cost_basis: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, server_default="0"
    )

    # CAS opening balance for a partial-period statement (0 for since-inception).
    opening_units: Mapped[float] = mapped_column(
        Numeric(18, 6), nullable=False, server_default="0"
    )
    opening_cost: Mapped[Optional[float]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    opening_as_of: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Ingest's ``derived_from_txns``: did the transaction ledger reproduce the
    # statement's own closing balance? False marks a scheme whose ledger is
    # known-incomplete, and is what routes it to the per-scheme anchor.
    ledger_agrees: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    first_txn_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    last_txn_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # The mf_nav_history.scheme_code this position actually prices against.
    nav_key: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
