"""The allocation reply discloses a saved preference — and stays silent on the
preference-free ideal fallback, and on a what-if that carries its own contrast."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

import app.domains.asset_allocation.services.aa_engine.chat as aa_chat


def _row():
    return SimpleNamespace(
        id=uuid.uuid4(),
        customer_choices={"class_mix": {}, "pins": []},
        asset_class_requested={"equity": 60.0, "debt": 30.0, "others": 10.0},
        resolved_targets={"high_beta_equities": 0.0},
    )


def _practical():
    """The PAA output AA displays: it carries the run's override report."""
    return SimpleNamespace(
        human_override_applied=SimpleNamespace(
            preference_applied=True, shortfall_reason=None
        )
    )


def _ctx(preference=None):
    return SimpleNamespace(
        user_ctx=SimpleNamespace(
            id=uuid.uuid4(),
            first_name="A",
            date_of_birth=None,
            personal_finance_profile=None,
            saved_investment_preference=preference,
        ),
        db=None,
        session_id=uuid.uuid4(),
        effective_user_id=uuid.uuid4(),
        tools_needed=(),
    )


@pytest.fixture
def pack_spy(monkeypatch):
    seen = {}

    async def _fake_format(**kw):
        seen.update(kw)
        return "reply"

    monkeypatch.setattr(aa_chat, "format_with_telemetry", _fake_format)
    monkeypatch.setattr(aa_chat, "build_aa_facts_pack", lambda *a, **k: dict(k))
    monkeypatch.setattr(aa_chat, "compute_current_asset_class_mix", lambda u: None)
    return seen


async def test_practical_display_discloses_the_preference(pack_spy):
    await aa_chat._format_or_fallback(
        ctx=_ctx(_row()),
        output=_practical(),
        action_mode="compute",
        spine_mode="standard",
    )

    block = pack_spy["facts_pack"]["active_preferences"]
    assert block["choices"][0] == "60% equity / 30% debt / 10% commodity"
    assert "nothing in small-cap equity" in block["choices"]


async def test_ideal_fallback_stays_silent(pack_spy):
    """The ideal model carries no human_override_applied — no claim allowed."""
    await aa_chat._format_or_fallback(
        ctx=_ctx(_row()),
        output=SimpleNamespace(),
        action_mode="compute",
        spine_mode="standard",
    )

    assert pack_spy["facts_pack"]["active_preferences"] is None


async def test_no_saved_preference_means_no_block(pack_spy):
    await aa_chat._format_or_fallback(
        ctx=_ctx(None),
        output=_practical(),
        action_mode="compute",
        spine_mode="standard",
    )

    assert pack_spy["facts_pack"]["active_preferences"] is None


async def test_a_what_if_contrast_suppresses_the_saved_disclosure(pack_spy):
    """preference_impact is the unsaved what-if's own contrast; claiming a
    saved preference beside it credits a save the customer never made."""
    await aa_chat._format_or_fallback(
        ctx=_ctx(_row()),
        output=_practical(),
        action_mode="counterfactual_explore",
        spine_mode="counterfactual",
        preference_impact={"recommended_mix_pct": {}, "requested_mix_pct": {}},
    )

    assert pack_spy["facts_pack"]["active_preferences"] is None
    assert pack_spy["facts_pack"]["preference_impact"] is not None
