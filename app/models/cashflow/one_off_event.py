"""One-off cashflow events (1:N per user)."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import BigInteger, Date, Enum as SAEnum, ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from typing import TYPE_CHECKING

from app.database import Base
from app.models.cashflow.enums import OneOffDirection

if TYPE_CHECKING:
    from app.models.user import User


class CashflowOneOffEvent(Base):
    __tablename__ = "cashflow_input_one_off_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    direction: Mapped[OneOffDirection] = mapped_column(
        SAEnum(
            OneOffDirection,
            name="one_off_direction_enum",
            values_callable=lambda e: [m.value for m in e],
            create_constraint=True,
        ),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)

    user: Mapped["User"] = relationship(back_populates="cashflow_one_off_events")
