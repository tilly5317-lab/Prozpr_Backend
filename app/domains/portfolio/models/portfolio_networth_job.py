"""Lifecycle row for a user's net-worth-history rebuild.

The frontend polls the latest row for this user to render a % completion bar, so this
is the job's public face as much as its bookkeeping.

Status: ``pending`` -> ``running`` -> ``success`` | ``failed``.
Phase:  ``queued`` -> ``fetching_nav`` -> ``computing`` -> ``persisting`` -> ``done``.

**This table is NOT CasScoped, and that is load-bearing.** It used to be, which meant a
running job vanished from every read the instant a second upload superseded the snapshot
it was stamped with — precisely when a second upload is most likely. ``has_running_job``
then returned ``None`` while the partial unique index still rejected the replacement
insert, so ``create_job`` re-raised the ``IntegrityError`` and the second upload 500'd.
A job belongs to a user, not to a statement.

Single-flight is enforced by the partial unique index ``uq_networth_job_active`` (see
``apply_postgres_schema_patches``), not by a check-then-create read — check-then-create
is a race, and it lost: one account accumulated three concurrent builds fighting over
the same rows.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PortfolioNetworthJob(Base):
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
    days_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Set when fresh data landed while this job was already running. The worker checks
    # it before finishing and runs one more pass, so a second CAS upload can never be
    # swallowed by joining an in-flight build of the statement it just replaced.
    supersede_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    # Degraded-data counters surfaced to the UI (stale prices, failed NAV fetches,
    # clamped values) — a build can succeed and still be worth qualifying.
    warnings: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    # cas_upload | manual | onboarding | daily
    trigger: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Doubles as the job's HEARTBEAT: ``reap_stale_jobs`` reads it to tell a live build
    # from one whose worker stopped existing (deploy restart, OOM).
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
