"""SQLAlchemy ORM model — `planning_write.py`.

The audit log for every plan change chat makes — a profile field OR a goal
row: what changed, from what, on whose say-so, and how confident the extractor
was. Two jobs:

  1. **Undo.** ``previous_value`` is what ``POST .../capture/undo`` restores.
     Without it an undo would have to guess, and guessing wrong on a financial
     input is worse than not offering undo at all.
  2. **Forensics.** When a customer's cashflow projection looks wrong six weeks
     later, this is the row that says an income of ₹28.8L came from a chat
     message at 0.62 confidence.

Values are JSONB so any registry type (money, enum string, date) round-trips
without a per-type column — and so a deleted GOAL can be stored whole in
``previous_value`` and put back verbatim. A goal row is recorded with
``table_name='goals'``, ``column_name='*'`` and the goal id in ``field_key``;
a profile field uses its registry key and its own column.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# Where the value came from — kept coarse on purpose; the capture run carries
# the detail.
SOURCE_CHAT_ANSWER = "chat_answer"  # answered a question PI asked
SOURCE_CHAT_VOLUNTEERED = "chat_volunteered"  # said without being asked
SOURCE_CHAT_RELATIVE = "chat_relative"  # "up 20%" — resolved against the stored value
SOURCE_CHAT_GOAL = "chat_goal"  # a goal row created, edited or removed in chat


class PlanningWrite(Base):
    # Table name predates the merge — see chat_planning_ask.py.
    __tablename__ = "profile_field_writes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    capture_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_profile_capture_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    field_key: Mapped[str] = mapped_column(String(64), nullable=False)
    table_name: Mapped[str] = mapped_column(String(64), nullable=False)
    column_name: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_value: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence: Mapped[Optional[float]] = mapped_column(Numeric(4, 3), nullable=True)
    # The words the value was read from — the only way to audit a bad parse.
    verbatim: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    undone_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
