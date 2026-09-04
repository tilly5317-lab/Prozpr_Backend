"""Standing customer investment preferences — the human_override source of truth.

IMMUTABLE, VERSIONED rows: every save inserts a new row and deactivates the
prior one; clear deactivates without deleting. Run
tables reference a row by FK (`saved_investment_preference_id`), so historical
runs keep pointing at exactly the values that shaped them. At most one active
row per user (partial unique index). The ONLY computation-time reader is the
shared PAA input builder (contract test in profile tests); the profile router
owns writes. Spec: 2026-09-01-investment-preferences-s1-core-design.md §4.1.

Class targets are flat Float columns (requested = the customer's ask,
target = what was achievable at save time; each triple sums to 100). Only the
genuinely variable-shape facets stay JSONB: `resolved_targets` (the
promised numbers) and `customer_choices` (the words that produced them) —
one subgroup facet, one vocabulary; market-cap asks land on the beta
subgroups.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SavedInvestmentPreference(Base):
    __tablename__ = "saved_investment_preferences"
    __table_args__ = (
        Index(
            "uq_saved_investment_preferences_active_user",
            "user_id",
            unique=True,
            postgresql_where=text("is_active"),
            sqlite_where=text("is_active"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )

    equity_requested_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    debt_requested_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    others_requested_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    equity_target_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    debt_target_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    others_target_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # What we PROMISED: {subgroup: % share of its own class}, resolved once
    # at save; 0 = hard exclusion. The engine's only subgroup input.
    resolved_targets: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    # What the customer SAID: the chips/words verbatim. Renders the screen
    # and powers save idempotence; never read by the engine.
    customer_choices: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    @property
    def asset_class_requested(self) -> Optional[dict]:
        if self.equity_requested_pct is None:
            return None
        return {
            "equity": self.equity_requested_pct,
            "debt": self.debt_requested_pct,
            "others": self.others_requested_pct,
        }

    @property
    def asset_class_target(self) -> Optional[dict]:
        if self.equity_target_pct is None:
            return None
        return {
            "equity": self.equity_target_pct,
            "debt": self.debt_target_pct,
            "others": self.others_target_pct,
        }
