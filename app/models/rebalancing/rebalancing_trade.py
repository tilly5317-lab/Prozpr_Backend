"""SQLAlchemy ORM — execution-ready BUY/SELL/EXIT actions for a rebalancing run.

Mirrors ``Rebalancing.models.TradeAction`` plus execution metadata so the
broker integration can mark each trade ``executed`` once it lands.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.rebalancing.rebalancing_run import RebalancingRun


class TradeAction(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"
    EXIT = "EXIT"


class TradeExecutionStatus(str, enum.Enum):
    pending = "pending"
    executed = "executed"
    skipped = "skipped"
    failed = "failed"


class RebalancingTrade(Base):
    __tablename__ = "rebalancing_trades"
    __table_args__ = (
        Index("ix_rebalancing_trades_run_action", "run_id", "action"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rebalancing_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    isin: Mapped[str] = mapped_column(String(20), nullable=False)
    recommended_fund: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_subgroup: Mapped[str] = mapped_column(String(80), nullable=False)
    sub_category: Mapped[str] = mapped_column(String(80), nullable=False)

    action: Mapped[TradeAction] = mapped_column(
        SAEnum(
            TradeAction,
            name="rebalancing_trade_action_enum",
            create_constraint=True,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
    )
    amount_inr: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)

    reason_code: Mapped[str] = mapped_column(String(80), nullable=False)
    reason_title: Mapped[str] = mapped_column(String(160), nullable=False)
    reason_text: Mapped[str] = mapped_column(Text, nullable=False)

    execution_status: Mapped[TradeExecutionStatus] = mapped_column(
        SAEnum(
            TradeExecutionStatus,
            name="rebalancing_trade_execution_status_enum",
            create_constraint=True,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        default=TradeExecutionStatus.pending,
    )
    executed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    broker_ref: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    run: Mapped["RebalancingRun"] = relationship(back_populates="trades")
