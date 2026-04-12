"""SQLAlchemy ORM model — `user_investment_list.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, UniqueConstraint, func, text as sa_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

from app.models.mf.enums import UserInvestmentListKind

if TYPE_CHECKING:
    from app.models.user import User


class UserInvestmentList(Base):
    """Per-client guardrail lists consumed by recommendation engines.

    Not a transaction/holding ledger. These lists represent advisory constraints
    such as temporary exit restrictions, STCG-sensitive lots, or blocked schemes.
    """

    __tablename__ = "user_investment_lists"
    __table_args__ = (UniqueConstraint("user_id", "list_kind", name="uq_user_investment_list_kind"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    list_kind: Mapped[UserInvestmentListKind] = mapped_column(
        SAEnum(UserInvestmentListKind, name="user_investment_list_kind_enum", create_constraint=True),
        nullable=False,
    )
    entries: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sa_text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="investment_lists")
