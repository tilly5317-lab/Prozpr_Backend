"""SQLAlchemy ORM model — `linked_account.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import User


class LinkedAccountType(str, enum.Enum):
    mutual_fund = "mutual_fund"
    bank_account = "bank_account"
    stock_demat = "stock_demat"
    other = "other"


class LinkedAccountStatus(str, enum.Enum):
    pending = "pending"
    active = "active"
    inactive = "inactive"
    failed = "failed"


class LinkedAccount(Base):
    __tablename__ = "linked_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )

    account_type: Mapped[LinkedAccountType] = mapped_column(
        SAEnum(LinkedAccountType, name="linked_account_type_enum", create_constraint=True)
    )
    provider_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    account_identifier: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    encrypted_access_token: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[LinkedAccountStatus] = mapped_column(
        SAEnum(LinkedAccountStatus, name="linked_account_status_enum", create_constraint=True),
        default=LinkedAccountStatus.pending,
    )
    metadata_json: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, nullable=True)
    linked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="linked_accounts")
