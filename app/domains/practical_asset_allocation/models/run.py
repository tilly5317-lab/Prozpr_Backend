"""SQLAlchemy ORM — persisted practical (holdings-aware) allocation runs.

Postgres table: ``practical_asset_allocation_runs``.

One row per execution of ``AI_Agents/src/practical_asset_allocation``. Unlike
the ideal ``asset_allocation_*`` tables (a normalized 4-table layout), the
practical engine's output is a single snapshot consumed by the rebalancing
flow, so we persist it as one header row: queryable scalars (corpus splits,
asset-class totals) plus the full engine output in ``result_payload`` for
faithful replay.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.domains.chat.models.chat import ChatSession
    from app.domains.identity.models.user import User


class PracticalAssetAllocationRun(Base):
    """One execution of the practical-allocation engine (persisted run header)."""

    __tablename__ = "practical_asset_allocation_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chat_session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    pipeline_source: Mapped[str] = mapped_column(
        String(80), nullable=False, default="practical_asset_allocation"
    )
    user_question: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ---- client summary -----------------------------------------------------
    client_age: Mapped[int] = mapped_column(Integer, nullable=False)
    client_occupation: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    client_effective_risk_score: Mapped[float] = mapped_column(
        Numeric(5, 2), nullable=False
    )
    total_corpus: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    grand_total: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    all_amounts_in_multiples_of_100: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # ---- recommended asset-class roll-up ------------------------------------
    equity_total: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    debt_total: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    others_total: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    equity_total_pct: Mapped[float] = mapped_column(Numeric(7, 2), nullable=False, default=0)
    debt_total_pct: Mapped[float] = mapped_column(Numeric(7, 2), nullable=False, default=0)
    others_total_pct: Mapped[float] = mapped_column(Numeric(7, 2), nullable=False, default=0)

    # ---- practical-only corpus breakdown ------------------------------------
    mf_corpus: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    non_mf_equity_input: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    non_mf_equity_actual: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    excess_direct_stocks: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    elss_corpus: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    rebalancing_corpus: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    max_non_mf_equity_pct_computed: Mapped[float] = mapped_column(
        Numeric(6, 4), nullable=False, default=0
    )

    # ---- payloads -----------------------------------------------------------
    input_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    result_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship()
    chat_session: Mapped[Optional["ChatSession"]] = relationship()
