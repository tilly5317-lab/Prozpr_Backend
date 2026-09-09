"""Per-user daily portfolio NAV history.

ONE ROW PER (user_id, recorded_date) — that grain is the whole contract, and it is
why this table is deliberately **not** CAS-snapshot-scoped.

A net-worth series is a *derived roll-up of whatever is currently effective*, not an
artifact owned by one statement. Scoping it was a real outage: a re-upload stamped the
old rows with the now-superseded snapshot, the read hook hid them, and users whose only
sin was uploading a second CAS saw their entire chart vanish (3,529 rows stored, 0
visible). The rebuild then deleted only the *new* snapshot's rows (none) and re-inserted
the whole window, which collided with the still-present old rows on
``uq_user_nav_history_user_date`` and killed the backfill job at 98%.

The two models cannot both be right: a snapshot-scoped table needs the snapshot in its
key, and a per-day series has no room for two values on the same day. The series wins —
it is fully recomputable from the ledger at any time, so there is nothing to version.

``cas_upload_id`` survives as PROVENANCE ONLY (which statement last rebuilt this row).
It must never become a scope key again; see ``app/core/cas_scope.py``.

Computed by ``app.domains.portfolio.services.networth_history_service`` from the real
transaction ledger (units × that day's NAV). Used by the portfolio dashboard chart.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserPortfolioNavHistory(Base):
    __tablename__ = "user_portfolio_nav_history"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "recorded_date", name="uq_user_nav_history_user_date"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    recorded_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # Provenance, NOT a scope key: the statement this row was last rebuilt from.
    # Declared by hand rather than via the CasScoped mixin precisely so the
    # read-filter and write-stamp hooks leave this table alone (see the module
    # docstring). Nullable and unindexed-by-choice — nothing queries on it.
    cas_upload_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cas_uploads.id", ondelete="SET NULL"),
        nullable=True,
    )
    total_value: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    total_invested: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, server_default="0"
    )
    gain_percentage: Mapped[float] = mapped_column(
        Numeric(10, 4), nullable=False, server_default="0"
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
