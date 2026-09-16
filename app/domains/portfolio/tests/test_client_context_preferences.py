"""The readout's facts: portfolio_query carries the customer's saved preference
in their own words. Measured 2026-09-16: this is where "what preferences do I
have set?" routes — cold (0.85) AND mid-conversation with a plan on screen
(0.92) — so it is the readout's only home."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.domains.portfolio.services.portfolio_query_service import (
    _build_client_context,
)


def _user(preference):
    return SimpleNamespace(
        id=uuid.uuid4(),
        date_of_birth=None,
        risk_profile=None,
        investment_profile=None,
        effective_risk_assessment=None,
        financial_goals=[],
        saved_investment_preference=preference,
    )


def test_saved_preference_reaches_the_client_context():
    pref = SimpleNamespace(
        customer_choices={"class_mix": {}, "pins": []},
        asset_class_requested={"equity": 60.0, "debt": 30.0, "others": 10.0},
        resolved_targets={"high_beta_equities": 0.0, "low_beta_equities": 30.0},
    )

    ctx = _build_client_context(_user(pref))

    assert ctx.investment_preferences[0] == "60% equity / 30% debt / 10% commodity"
    assert "no small-cap equity" in ctx.investment_preferences
    # Engine keys must never reach the agent's facts.
    assert not any("beta_equities" in c for c in ctx.investment_preferences)


def test_no_preference_is_an_empty_list_not_a_guess():
    ctx = _build_client_context(_user(None))

    assert ctx.investment_preferences == []
