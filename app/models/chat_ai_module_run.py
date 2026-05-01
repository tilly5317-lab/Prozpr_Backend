"""SQLAlchemy ORM model — `chat_ai_module_run.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.chat import ChatSession
    from app.models.user import User


class ChatAiModuleRun(Base):
    __tablename__ = "chat_ai_module_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )

    module: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    intent_detected: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    spine_mode: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    extra: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    input_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    output_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    formatter_invoked: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    formatter_succeeded: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    formatter_latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    formatter_error_class: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    action_mode: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="ai_module_runs")
    session: Mapped[Optional["ChatSession"]] = relationship(back_populates="ai_module_runs")
