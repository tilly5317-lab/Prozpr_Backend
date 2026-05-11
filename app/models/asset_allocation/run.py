"""Asset-allocation run table model."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.asset_allocation.aggregate import AssetAllocationAggregate
    from app.models.asset_allocation.bucket import AssetAllocationBucket
    from app.models.chat import ChatSession
    from app.models.portfolio import Portfolio
    from app.models.rebalancing.rebalancing_run import RebalancingRun
    from app.models.user import User


class AssetAllocationRunStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    superseded = "superseded"
    rejected = "rejected"


class AssetAllocationRun(Base):
    __tablename__ = "asset_allocation_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    portfolio_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("portfolios.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    chat_session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    supersedes_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("asset_allocation_runs.id", ondelete="SET NULL"),
        nullable=True,
    )

    status: Mapped[AssetAllocationRunStatus] = mapped_column(
        SAEnum(
            AssetAllocationRunStatus,
            name="asset_allocation_run_status_enum",
            create_constraint=True,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        default=AssetAllocationRunStatus.pending,
    )

    pipeline_source: Mapped[str] = mapped_column(
        String(80), nullable=False, default="asset_allocation_pydantic"
    )
    spine_mode: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    user_question: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    input_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

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

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="asset_allocation_runs")
    portfolio: Mapped[Optional["Portfolio"]] = relationship()
    chat_session: Mapped[Optional["ChatSession"]] = relationship()
    superseded_by: Mapped[Optional["AssetAllocationRun"]] = relationship(
        "AssetAllocationRun",
        remote_side="AssetAllocationRun.id",
        foreign_keys=[supersedes_id],
    )

    buckets: Mapped[List["AssetAllocationBucket"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    aggregates: Mapped[List["AssetAllocationAggregate"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    rebalancing_runs: Mapped[List["RebalancingRun"]] = relationship(
        back_populates="source_allocation_run"
    )
