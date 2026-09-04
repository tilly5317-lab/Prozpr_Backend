"""Run-level preference provenance (spec §4.1).

A run that consumed a saved preference stores the FK of the IMMUTABLE
preference row that shaped it (`saved_investment_preference_id`);
NULL = computed with no preference. Rows are never mutated or deleted
(clear/supersede just flips `is_active`), so the FK is a permanent,
truthful snapshot — the values are one join away. A one-off chat override
with no saved row persists NULL.
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.profile.models.saved_investment_preference import (
    SavedInvestmentPreference,
)


async def active_preference_id(
    db: AsyncSession, user_id, *, applied: bool
) -> Optional[uuid.UUID]:
    """The user's active preference row id, or None.

    ``applied`` is the caller's signal that the run actually consumed a
    preference (e.g. ``result.human_override_applied is not None`` or a
    non-empty engine-input override) — when False the run is tagged NULL
    even if a row exists (the run was computed neutral, e.g. bypass path).
    """
    if not applied or user_id is None:
        return None
    stmt = select(SavedInvestmentPreference.id).where(
        SavedInvestmentPreference.user_id == user_id,
        SavedInvestmentPreference.is_active.is_(True),
    )
    return (await db.execute(stmt)).scalars().first()


def preference_id_for(user, *, applied: bool) -> Optional[uuid.UUID]:
    """Sync variant for call sites that hold the loaded ``user`` object —
    reads the eager-loaded active-row relationship, no DB round trip."""
    if not applied:
        return None
    row = getattr(user, "saved_investment_preference", None)
    return getattr(row, "id", None)
