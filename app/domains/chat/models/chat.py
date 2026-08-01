"""SQLAlchemy ORM model — `chat.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    SmallInteger,
    String,
    Text,
    case,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.domains.cashflow.models.plan_run import CashflowPlanRun
    from app.domains.chat.models.chat_ai_module_run import ChatAiModuleRun
    from app.domains.identity.models.user import User


class ChatSessionStatus(str, enum.Enum):
    active = "active"
    closed = "closed"


class ChatMessageRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )

    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[ChatSessionStatus] = mapped_column(
        SAEnum(
            ChatSessionStatus, name="chat_session_status_enum", create_constraint=True
        ),
        default=ChatSessionStatus.active,
    )

    # User's 1–5 star rating of Pi for this conversation; null until rated.
    # One rating per session — set via PATCH /chat/sessions/{id}/rating, so the
    # frontend can stop re-prompting once a session has been rated.
    rating: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="chat_sessions")
    messages: Mapped[List["ChatMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        # Lambda, not a string: the tiebreak needs ChatMessage, defined below.
        # Resolved at mapper-configuration time, so the forward reference is fine.
        order_by=lambda: [ChatMessage.created_at, message_role_rank()],
    )
    ai_module_runs: Mapped[List["ChatAiModuleRun"]] = relationship(
        back_populates="session"
    )
    cashflow_plan_runs: Mapped[List["CashflowPlanRun"]] = relationship(
        back_populates="chat_session"
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE")
    )

    role: Mapped[ChatMessageRole] = mapped_column(
        SAEnum(ChatMessageRole, name="chat_message_role_enum", create_constraint=True),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Classified intent of the turn that produced this ASSISTANT message
    # (NULL on user rows and on rows written before this column shipped).
    # Written by chat_router.send_message; read by the intent classifier's
    # history scrub to drop canned-redirect turns by tag instead of matching
    # message-text prefixes, and picked up by ChatMessageResponse.intent so
    # reloaded sessions keep per-message intent.
    intent: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    session: Mapped["ChatSession"] = relationship(back_populates="messages")


def message_role_rank():
    """Sort key placing a turn's user row before its assistant row.

    Both rows of a turn are written in ONE transaction and ``created_at`` is
    ``server_default=func.now()`` — the TRANSACTION timestamp in Postgres — so
    they carry byte-identical timestamps and ``ORDER BY created_at`` alone is
    ambiguous. Whichever way that tie resolved, nothing downstream could tell:
    on 2026-07-25 it resolved assistant-first and the prompt history handed the
    portfolio_query agent a dangling user message, which sent it down its
    goal-planning guardrail instead of answering the question asked.

    Every ordering of ``chat_messages`` must carry this tiebreak. Sort DESCENDING
    on it wherever ``created_at`` is descending, so a later reversal still lands
    question-before-answer.
    """
    return case((ChatMessage.role == ChatMessageRole.user, 0), else_=1)
