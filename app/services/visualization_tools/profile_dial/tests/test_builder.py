"""Smoke test for the profile_dial chart builder."""
from __future__ import annotations

import uuid

import pytest

from app.models.profile.effective_risk_assessment import EffectiveRiskAssessment


@pytest.mark.asyncio
async def test_returns_none_when_no_assessment(db_session, fixture_user_with_dob):
    from app.services.visualization_tools.profile_dial.builder import (
        build_profile_dial,
    )
    out = await build_profile_dial(db_session, fixture_user_with_dob.id)
    assert out is None


@pytest.mark.asyncio
async def test_returns_dial_with_band(db_session, fixture_user_with_dob):
    user = fixture_user_with_dob
    db_session.add(EffectiveRiskAssessment(
        id=uuid.uuid4(),
        user_id=user.id,
        step_name="risk_profile",
        payload={},
        calculations={},
        output={},
        effective_risk_score=72.0,
    ))
    await db_session.flush()

    from app.services.visualization_tools.profile_dial.builder import (
        build_profile_dial,
    )
    out = await build_profile_dial(db_session, user.id)
    assert out is not None
    assert out.type == "profile_dial"
    assert out.score == 72.0
    assert out.band in {"Conservative", "Moderate-Conservative", "Balanced",
                        "Moderate-Aggressive", "Aggressive"}
    # 72 sits in the Moderate-Aggressive band (60-80)
    assert out.band == "Moderate-Aggressive"
