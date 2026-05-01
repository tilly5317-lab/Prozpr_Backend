"""End-to-end: REBALANCING intent → handler → trade-list reply.

Scope (per plan §Task 12 fallback): integration-flavour. We bypass the LLM
classifier and call ``dispatch_chat('rebalancing', ...)`` directly after
seeding all the rows the service needs to short-circuit on the cache hit.
"""

from __future__ import annotations

import uuid

import pytest


@pytest.mark.asyncio
async def test_rebalancing_chat_dispatch_returns_sectioned_markdown(
    db_session,
    fixture_user_with_holdings,
    fixture_recent_allocation_row,
    fixture_seed_low_beta_navs,
    fixture_one_subgroup_ranking,
):
    # Side-effect import: registers the @register('rebalancing') handler.
    import app.services.ai_bridge.rebalancing.chat  # noqa: F401
    from app.services.ai_bridge.chat_dispatcher import dispatch_chat
    from app.services.chat_core.turn_context import TurnContext

    user, _ = fixture_user_with_holdings
    ctx = TurnContext(
        user_ctx=user,
        user_question="rebalance my portfolio",
        conversation_history=[],
        client_context=None,
        session_id=uuid.uuid4(),
        db=db_session,
        effective_user_id=user.id,
        last_agent_runs={},
        active_intent="rebalancing",
    )

    result = await dispatch_chat("rebalancing", ctx)

    text_lower = result.text.lower()
    # Cache hit → soft lead line is omitted.
    assert "asset mix" not in text_lower
    # Closing line is always present.
    assert "sanity check" in text_lower
    # Header mentions the rebalance / corpus framing.
    assert "rebalance" in text_lower or "trade" in text_lower
    # Recommendation row was persisted and forwarded.
    assert result.rebalancing_recommendation_id is not None
