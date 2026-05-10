"""SQLAlchemy ORM — engine warnings emitted during a rebalancing run.

Mirrors ``Rebalancing.models.RebalancingWarning``. Codes are stable
analytics keys; the human-readable ``message`` is what we surface to support /
internal dashboards.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import String

from app.database import Base

if TYPE_CHECKING:
    from app.models.rebalancing.rebalancing_run import RebalancingRun


class RebalancingWarningCode(str, enum.Enum):
    UNREBALANCED_REMAINDER = "UNREBALANCED_REMAINDER"
    BAD_FUND_DETECTED = "BAD_FUND_DETECTED"
    STCG_BUDGET_BINDING = "STCG_BUDGET_BINDING"
    NO_HOLDINGS_FOR_RECOMMENDED_FUND = "NO_HOLDINGS_FOR_RECOMMENDED_FUND"


class RebalancingWarning(Base):
    __tablename__ = "rebalancing_warnings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rebalancing_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    code: Mapped[RebalancingWarningCode] = mapped_column(
        SAEnum(
            RebalancingWarningCode,
            name="rebalancing_warning_code_enum",
            create_constraint=True,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        index=True,
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    affected_isins: Mapped[list[str]] = mapped_column(
        ARRAY(String(20)), nullable=False, default=list
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    run: Mapped["RebalancingRun"] = relationship(back_populates="warnings")
