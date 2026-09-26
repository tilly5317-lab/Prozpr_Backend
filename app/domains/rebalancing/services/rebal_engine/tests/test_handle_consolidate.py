"""Tests for the stateless _consolidate handler branch.

Drives _consolidate directly with a stub ctx; compute_rebalancing_result and
format_with_telemetry are patched so no DB / LLM is touched. Asserts the engine
runs with persist=False, the facts pack carries reshaped buys + constraint_impact,
and nothing is persisted.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.domains.ai_engine.common import ensure_ai_agents_path

ensure_ai_agents_path()

from Rebalancing.Testing.consolidation_helpers import minimal_response_with_buys  # noqa: E402

import app.domains.rebalancing.services.rebal_engine.chat as chat  # noqa: E402
from app.domains.rebalancing.services.rebal_engine.chat import (  # noqa: E402
    RebalanceAction,
    _consolidate,
)
from app.domains.rebalancing.services.rebal_engine.service import (  # noqa: E402
    RebalancingRunOutcome,
)

_CHAT = "app.domains.rebalancing.services.rebal_engine.chat"


def _ctx():
    return SimpleNamespace(
        user_ctx=SimpleNamespace(risk_profile="Moderate", first_name="Amoul"),
        user_question="consolidate my plan",
        db=None,
        effective_user_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
    )


def _outcome_with_buys():
    resp = minimal_response_with_buys(
        buys=[("A", "Large Cap Fund", 1, 40000),
              ("B", "Mid Cap Fund", 1, 30000),
              ("C", "Gold ETF", 1, 30000)],
        sells=[("Z", "Focused Fund", 50000)],
    )
    return RebalancingRunOutcome(response=resp, formatted_text="fallback")


@pytest.mark.asyncio
async def test_consolidate_reshapes_narrates_no_persist():
    captured = {}

    async def _fake_format(**kwargs):
        captured["facts_pack"] = kwargs["facts_pack"]
        captured["action_mode"] = kwargs["action_mode"]
        captured["fallback"] = kwargs["build_fallback"]()   # invoke the degraded path
        return "NARRATED"

    with patch(f"{_CHAT}.compute_rebalancing_result",
               new=AsyncMock(return_value=_outcome_with_buys())) as mock_compute, \
         patch(f"{_CHAT}.format_with_telemetry", new=AsyncMock(side_effect=_fake_format)):
        result = await _consolidate(
            _ctx(), RebalanceAction(mode="consolidate", target_fund_count=2))

    # engine ran ONCE, compute-only
    mock_compute.assert_awaited_once()
    assert mock_compute.await_args.kwargs["persist"] is False
    # nothing persisted
    assert result.rebalancing_recommendation_id is None
    assert result.text == "NARRATED"
    assert captured["action_mode"] == "consolidate"
    # A/Large Cap Fund, B/Mid Cap Fund, C/Gold ETF are 3 DISTINCT asset
    # subgroups, so the subgroup-aware floor (#subgroups-with-buys) beats the
    # requested target_fund_count=2: all 3 survive, total preserved, and the
    # bump to 3 is disclosed via constraint_impact.
    pack = captured["facts_pack"]
    # Output rows carry only _indian now; total preserved is read from the top-level.
    buys = {fa["fund_name"] for fa in pack["fund_actions"] if fa["buy_indian"] != "₹0"}
    assert len(buys) == 3
    assert pack["buys_total_indian"] == "₹1 lakh"
    assert "constraint_impact" in pack
    assert pack["constraint_impact"]["risk_profile"] == "Moderate"
    assert pack["constraint_impact"]["count_bumped_to"] == 3
    # the degraded fallback must be built from the RESHAPED plan, NOT the
    # original outcome.formatted_text ("fallback") — else a formatter failure
    # would show un-consolidated trades.
    assert captured["fallback"] != "fallback"
    assert isinstance(captured["fallback"], str) and captured["fallback"]


@pytest.mark.asyncio
async def test_incomplete_ask_clarifies_once_no_compute():
    # Asked through the formatter (action_mode="gather"), not returned raw —
    # returning it raw meant no telemetry row, so nothing downstream could tell
    # we had already asked.
    with (
        patch(f"{_CHAT}.compute_rebalancing_result", new=AsyncMock()) as mock_compute,
        patch(f"{_CHAT}._last_action_mode", new=AsyncMock(return_value=None)),
        patch(f"{_CHAT}.format_relay_or_canned",
              new=AsyncMock(return_value="how many funds?")) as relay,
    ):
        result = await _consolidate(
            _ctx(), RebalanceAction(mode="consolidate"))   # no count, no categories
    mock_compute.assert_not_awaited()
    assert result.text == "how many funds?"
    assert relay.await_args.kwargs["action_mode"] == "gather"
    assert relay.await_args.kwargs["message"] == chat._CONSOLIDATE_CLARIFY
    assert result.rebalancing_recommendation_id is None


@pytest.mark.asyncio
async def test_second_incomplete_ask_uses_a_default_instead_of_re_asking():
    # "as few as possible" / "you decide" carries no number, so the detector
    # emits consolidate with empty fields again. Repeating the identical question
    # leaves the customer stuck — do the work with a stated default instead.
    captured = {}

    async def _fake_format(**kwargs):
        captured.update(kwargs)
        return "consolidated"

    with (
        patch(f"{_CHAT}.compute_rebalancing_result",
              new=AsyncMock(return_value=_outcome_with_buys())),
        patch(f"{_CHAT}._last_action_mode", new=AsyncMock(return_value="gather")),
        patch(f"{_CHAT}.format_relay_or_canned", new=AsyncMock()) as relay,
        patch(f"{_CHAT}._format_or_fallback_rebal", new=_fake_format),
    ):
        result = await _consolidate(
            _ctx(), RebalanceAction(mode="consolidate"))

    relay.assert_not_awaited()                       # did NOT ask again
    assert result.text == "consolidated"
    assert captured["action_mode"] == "consolidate"  # it actually did the work
    # And the reply is told we chose the number, so it can own that.
    assert captured["constraint_impact"]["defaulted_fund_count"] == (
        chat._DEFAULT_CONSOLIDATE_FUND_COUNT
    )


@pytest.mark.asyncio
async def test_a_normal_consolidate_carries_no_defaulted_flag():
    captured = {}

    async def _fake_format(**kwargs):
        captured.update(kwargs)
        return "consolidated"

    with (
        patch(f"{_CHAT}.compute_rebalancing_result",
              new=AsyncMock(return_value=_outcome_with_buys())),
        patch(f"{_CHAT}._format_or_fallback_rebal", new=_fake_format),
    ):
        await _consolidate(
            _ctx(), RebalanceAction(mode="consolidate", target_fund_count=2))
    assert "defaulted_fund_count" not in captured["constraint_impact"]


@pytest.mark.asyncio
async def test_category_not_in_plan_is_honest():
    with patch(f"{_CHAT}.compute_rebalancing_result",
               new=AsyncMock(return_value=_outcome_with_buys())):
        result = await _consolidate(
            _ctx(),
            RebalanceAction(mode="consolidate", allowed_categories=["small cap"]))
    # Small Cap Fund resolves but isn't in the plan's buys → honest, no fabrication
    assert "doesn't buy into" in result.text
    assert result.rebalancing_recommendation_id is None


@pytest.mark.asyncio
async def test_unresolvable_category_clarifies():
    with patch(f"{_CHAT}.compute_rebalancing_result", new=AsyncMock()) as mock_compute:
        result = await _consolidate(
            _ctx(),
            RebalanceAction(mode="consolidate", allowed_categories=["crypto"]))
    mock_compute.assert_not_awaited()
    assert "couldn't match" in result.text
