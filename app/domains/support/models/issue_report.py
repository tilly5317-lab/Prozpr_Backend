"""SQLAlchemy ORM model — `issue_report.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IssueReport(Base):
    """A user-filed issue report. The DB row is the source of truth; the Excel
    log and the support email are derived/notified copies."""

    __tablename__ = "issue_reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )

    # Denormalized so the report survives user edits/deletion intact.
    user_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    user_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    source: Mapped[str] = mapped_column(String(50), nullable=False)
    # User-typed location of the issue; required (enforced in the router) when source == "Other".
    source_detail: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    screenshot_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
