"""Tracks the post-signup "Generate my portfolio" personalisation job.

A row records the lifecycle of the background job kicked off when the user taps
"Generate my portfolio" at the end of onboarding: effective-risk recalculation
and the daily net-worth NAV history build. The loading page polls the latest
row for this user to render a real progress bar and per-task checklist.

Status: ``pending`` -> ``running`` -> ``success`` | ``failed``.
Phase:  ``queued`` -> ``risk`` -> ``networth`` -> ``done``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class OnboardingGenerationJob(Base):
    __tablename__ = "onboarding_generation_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="pending"
    )
    phase: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="queued"
    )
    progress_pct: Mapped[float] = mapped_column(
        Numeric(5, 2), nullable=False, server_default="0"
    )
    message: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)

    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
