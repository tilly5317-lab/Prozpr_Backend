"""SQLAlchemy ORM model — `plan_summary.py`.

LLM-generated narrative payload for a single plan run (1:1 with
`cashflow_plan_runs`). Mirrors `PlanSummary` in
`AI_Agents/src/cashflow_statement/models.py`; nested lists kept as `JSONB` so
the viewer reads them verbatim.
"""


from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.cashflow.plan_run import CashflowPlanRun


class CashflowPlanSummary(Base):
    __tablename__ = "cashflow_plan_summary"

    plan_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cashflow_plan_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )

    top_line: Mapped[str] = mapped_column(Text, nullable=False)
    retirement_note: Mapped[str] = mapped_column(Text, nullable=False)
    cashflow_note: Mapped[str] = mapped_column(Text, nullable=False)
    goals: Mapped[list] = mapped_column(JSONB, nullable=False)
    risks: Mapped[list] = mapped_column(JSONB, nullable=False)
    next_steps: Mapped[list] = mapped_column(JSONB, nullable=False)
    summary_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    plan_run: Mapped["CashflowPlanRun"] = relationship(back_populates="plan_summary")
