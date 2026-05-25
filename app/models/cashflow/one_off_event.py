"""SQLAlchemy ORM model — `one_off_event.py`.

User-scoped one-off inflow/outflow events consumed by the cashflow engine.
Mirrors `OneOffEvent` in `AI_Agents/src/cashflow_statement/models.py`, with an
explicit direction column instead of two parallel lists.
"""


from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Numeric,
    Text,
    func,
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.cashflow.enums import OneOffDirection

if TYPE_CHECKING:
    from app.models.user import User


class CashflowInputOneOffEvent(Base):
    __tablename__ = "cashflow_input_one_off_events"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_cf_one_off_amount_positive"),
        Index(
            "uq_one_off_description_per_user",
            "user_id",
            "direction",
            func.lower(sa_text("description")),
            unique=True,
        ),
        Index("ix_one_off_events_user_date", "user_id", "event_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    direction: Mapped[OneOffDirection] = mapped_column(
        SAEnum(OneOffDirection, name="one_off_direction_enum", create_constraint=True),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)

    user: Mapped["User"] = relationship(back_populates="cashflow_one_off_events")
