"""SQLAlchemy ORM model — `headline.py`.

Per-run headline status (1:1 with `cashflow_plan_runs`). Mirrors
`HeadlineStatus` in `AI_Agents/src/cashflow_statement/models.py` — the payload
exposed by `viewer.html`'s Headline tab.
"""


from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, ForeignKey, Numeric, SmallInteger
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.cashflow.plan_run import CashflowPlanRun


class CashflowHeadline(Base):
    __tablename__ = "cashflow_headline"
    __table_args__ = (
        CheckConstraint("total_shortfall_fv >= 0", name="ck_headline_shortfall_nonneg"),
        CheckConstraint("total_funded_amount >= 0", name="ck_headline_funded_nonneg"),
    )

    plan_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cashflow_plan_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    years_to_last_goal: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    last_goal_date: Mapped[date] = mapped_column(Date, nullable=False)
    last_fy_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    number_of_goals: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    corpus_today: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    total_corpus_required_today: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    surplus_or_shortfall_today: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    corpus_closing: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    total_shortfall_fv: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    total_funded_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)

    plan_run: Mapped["CashflowPlanRun"] = relationship(back_populates="headline")
