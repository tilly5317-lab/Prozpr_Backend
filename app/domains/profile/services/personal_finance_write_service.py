"""Writers for the canonical household-finance scalars.

``profile_finance`` reads these fields; this module is the write counterpart. It
exists so domains that own a *derived* view of a canonical field (e.g. the
additional-investment SIP plan) can update it without reaching into
``personal_finance_profiles`` themselves — the profile domain owns that table.

Commit-free: the caller owns the transaction.
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.profile.models.personal_finance_profile import (
    PersonalFinanceProfile,
)


async def set_starting_monthly_investment(
    db: AsyncSession, user_id: uuid.UUID, amount_inr: Optional[float]
) -> None:
    """Set the user's canonical monthly SIP (``starting_monthly_investment``).

    This single field is what the cashflow / goal-planning engine, the IPS view
    and the goal timeline all read, so every surface that lets a customer change
    their monthly SIP must write it here rather than keep a private copy.

    Creates the profile row when the user has none yet. Callers that change a
    cashflow input must also invalidate the cached plan run (``mark_stale``).
    """
    profile = (
        await db.execute(
            select(PersonalFinanceProfile).where(
                PersonalFinanceProfile.user_id == user_id
            )
        )
    ).scalar_one_or_none()

    if profile is None:
        profile = PersonalFinanceProfile(user_id=user_id)
        db.add(profile)

    profile.starting_monthly_investment = amount_inr
    await db.flush()


__all__ = ["set_starting_monthly_investment"]
