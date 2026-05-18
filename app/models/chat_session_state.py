"""SQLAlchemy ORM model — `chat_session_state.py`.

Per-session cross-turn state for chat handlers. One row per chat session,
upserted by handlers (e.g., counterfactual_explore sets awaiting_save=True;
save_last_counterfactual gates on it and resets to False on success).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.chat import ChatSession


class ChatSessionState(Base):
    __tablename__ = "chat_session_state"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    awaiting_save: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False,
    )
    last_counterfactual_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_ai_module_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )

    session: Mapped["ChatSession"] = relationship()
