"""SQLAlchemy ORM model — `chat_goal_draft.py`.

One row per goal the customer is building in conversation. The draft is
deliberately NOT a ``financial_goals`` row until they confirm: a half-specified
goal in the goals list would be picked up by the cashflow engine and quietly
change their plan while they were still deciding what car to buy.

Slots live in JSONB rather than columns because the set is a conversation, not
a schema — it grows as the customer volunteers things, and only the confirmed
projection is mapped onto the typed ``financial_goals`` columns at commit.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# Where the conversation is.
STAGE_COLLECTING = "collecting"  # still asking for slots
STAGE_CONFIRMING = "confirming"  # projection shown, waiting for yes/no
# Written to `goals`, and we have asked the one follow-up that could change
# the verdict. The gate keeps owning the thread through this stage so the
# customer's "no, everything's the same" lands here and not in some other flow.
STAGE_FOLLOW_UP = "follow_up"
STAGE_COMMITTED = "committed"  # written, and the thread is closed
STAGE_ABANDONED = "abandoned"  # customer backed out or changed the subject


class ChatGoalDraft(Base):
    __tablename__ = "chat_goal_drafts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=STAGE_COLLECTING,
        server_default=STAGE_COLLECTING,
    )
    # Everything gathered so far, keyed by slot name. See goal_slot_extractor.
    slots: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    # The last projection shown to the customer, so a "yes" commits exactly the
    # numbers they saw rather than a fresh calculation that may have drifted.
    projection: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    # The message that started this draft, kept for the audit trail.
    origin_question: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    committed_goal_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("goals.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
