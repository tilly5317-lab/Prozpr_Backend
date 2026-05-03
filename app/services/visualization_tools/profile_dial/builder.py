"""Chart builder — risk profile dial.

Reads the user's latest ``EffectiveRiskAssessment.effective_risk_score`` and
returns a ProfileDial payload with the score (0-100), the named band, and a
short headline. Returns None when no assessment exists yet.

5-band mapping (matches the existing risk-profiling vocabulary):
  0-20:  Conservative
  20-40: Moderate-Conservative
  40-60: Balanced
  60-80: Moderate-Aggressive
  80-100: Aggressive
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.profile.effective_risk_assessment import EffectiveRiskAssessment
from app.services.visualization_tools.profile_dial.schema import ProfileDial


_BANDS: list[tuple[float, str]] = [
    (20.0, "Conservative"),
    (40.0, "Moderate-Conservative"),
    (60.0, "Balanced"),
    (80.0, "Moderate-Aggressive"),
    (100.01, "Aggressive"),
]


def _band_for(score: float) -> str:
    for upper, label in _BANDS:
        if score < upper:
            return label
    return "Aggressive"


async def build_profile_dial(
    db: AsyncSession, user_id: uuid.UUID
) -> ProfileDial | None:
    """Build the risk-profile dial payload, or None if no assessment exists."""
    stmt = (
        select(EffectiveRiskAssessment)
        .where(EffectiveRiskAssessment.user_id == user_id)
        .where(EffectiveRiskAssessment.effective_risk_score.isnot(None))
        .order_by(EffectiveRiskAssessment.computed_at.desc())
        .limit(1)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None or row.effective_risk_score is None:
        return None

    score = float(row.effective_risk_score)
    score = max(0.0, min(100.0, score))
    band = _band_for(score)

    return ProfileDial(
        title="Your risk profile",
        subtitle="Based on your latest assessment",
        score=score,
        band=band,
        headline=f"You're in the {band} band ({score:.0f} / 100)",
    )
