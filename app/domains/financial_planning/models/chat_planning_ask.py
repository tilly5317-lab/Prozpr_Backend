"""SQLAlchemy ORM model — `chat_planning_ask.py`.

One row per question PI asks. The newest row with ``status='pending'`` is the
open question for that session; everything else is history, and the row count
per session is the ask budget the gate enforces.

One row per question PI asks about a plan input, and the staging area for
whatever it understood before the customer said yes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, SmallInteger, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# Lifecycle of one ask.
STATUS_PENDING = "pending"
# Read back to the customer, waiting for a yes. Nothing is written yet.
STATUS_CONFIRMING = "confirming"
STATUS_ANSWERED = "answered"
STATUS_SKIPPED = "skipped"
STATUS_CANCELLED = "cancelled"


class ChatPlanningAsk(Base):
    # Table name predates the merge and is deliberately unchanged: renaming it
    # would need a migration on a DB whose Alembic graph cannot be run
    # (see app/core/database.py apply_postgres_schema_patches).
    __tablename__ = "chat_profile_capture_runs"

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
    # Registry key of the field this ask is about.
    field_key: Mapped[str] = mapped_column(String(64), nullable=False)
    # The intent whose turn was spent asking — replayed once the answer lands.
    resume_intent: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # The question the customer ACTUALLY asked, kept verbatim. On resume the
    # engine must be re-run against this, not against the answer text: replaying
    # "5+ years" through goal planning produced "what are you asking about 5+
    # years?" because the engine had lost the antecedent.
    origin_question: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=STATUS_PENDING, server_default=STATUS_PENDING
    )
    # "hard" (blocked the engine) or "soft" (appended to a finished answer).
    ask_kind: Mapped[str] = mapped_column(
        String(8), nullable=False, default="hard", server_default="hard"
    )
    # What we understood, held here until the customer says yes. NOTHING from a
    # chat message reaches a profile table before that: the customer is talking,
    # not filling in a form, and a value we merely inferred from a sentence is a
    # proposal until they agree it is right.
    staged_values: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )
    # Wrong-answer / re-ask counter, so a field the extractor keeps failing on
    # is abandoned rather than asked forever.
    attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    asked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Set when the customer declines: the gate ignores this field until then.
    deferred_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
