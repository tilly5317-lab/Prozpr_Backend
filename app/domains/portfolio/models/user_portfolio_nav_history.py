"""Per-user daily portfolio net-worth series.

ONE ROW PER (user_id, recorded_date) — that grain is the whole contract, and it is
why this table is deliberately **not** CAS-snapshot-scoped.

A net-worth series is a *derived roll-up of whatever is currently effective*, not an
artifact owned by one statement. Scoping it was a real outage: a re-upload stamped the
old rows with the now-superseded snapshot, the read hook hid them, and users whose only
sin was uploading a second CAS saw their entire chart vanish (3,529 rows stored, 0
visible). The two models cannot both be right — a snapshot-scoped table needs the
snapshot in its key, and a per-day series has no room for two values on the same day.
The series wins: it is fully recomputable from the ledger at any time, so there is
nothing to version.

There is deliberately **no ``cas_upload_id`` column**. It survived one iteration as
"provenance only" plus a startup repair that nulled it for superseded snapshots — a
footgun with no reader. Provenance lives on ``user_networth_series_state`` instead.

Computed by ``app.domains.portfolio.services.networth`` from the real transaction
ledger (units x that day's NAV). Read by the portfolio dashboard chart and the TWR tab.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserPortfolioNavHistory(Base):
    __tablename__ = "user_portfolio_nav_history"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "recorded_date", name="uq_user_nav_history_user_date"
        ),
        # Every read is "this user, this date window" — horizon slices and
        # MAX(recorded_date) both go index-only on this.
        Index("ix_nav_hist_user_date", "user_id", "recorded_date"),
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
