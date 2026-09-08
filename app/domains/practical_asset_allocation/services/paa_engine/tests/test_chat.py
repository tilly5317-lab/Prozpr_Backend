"""Unit tests for the practical_asset_allocation chat handler (I1 fix wave;
reworked for the 2026-09-04 FK restructure).

Finding I1: ``persist_practical_allocation_run`` was called here without a
preference tag, so a chat-triggered practical allocation for a
preference-holding customer wrote NULL — which spec §4.1 defines as "computed
with no preference". These tests target the call wiring directly: the handler
must pass ``saved_investment_preference_id`` from ``active_preference_id``,
with ``applied`` reflecting whether the run actually consumed a preference.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.domains.ai_engine.turn_context import TurnContext


def _user_with_pref(pref_id):
    user = MagicMock()
    if pref_id is None:
        user.saved_investment_preference = None
    else:
        user.saved_investment_preference = SimpleNamespace(id=pref_id)
    return user


def _ctx(user) -> TurnContext:
    return TurnContext(
        user_ctx=user,
        user_question="rebalance me",
        conversation_history=[],
        client_context=None,
        session_id=uuid.uuid4(),
        db=MagicMock(),
        effective_user_id=uuid.uuid4(),
        last_agent_runs={},
        active_intent="practical_asset_allocation",
    )


def _result(human_override_applied=None):
    """Stand-in for a PracticalAllocationOutput — carries only the attribute
    the handler's tag-building reads."""
    return SimpleNamespace(human_override_applied=human_override_applied)


def _patched_handle(outcome, persist):
    from app.domains.practical_asset_allocation.services.paa_engine import (
        chat as paa_chat,
    )

    return (
        paa_chat,
        patch.object(
            paa_chat,
            "compute_practical_allocation_result",
            new=AsyncMock(return_value=outcome),
        ),
        patch.object(paa_chat, "persist_practical_allocation_run", new=persist),
        patch.object(paa_chat, "build_practical_fallback_brief", return_value="brief"),
    )


def test_handle_passes_preference_fk_to_persist():
    from app.domains.practical_asset_allocation.services.paa_engine.service import (
        PracticalAllocationRunOutcome,
    )
    from practical_asset_allocation.human_override import HumanOverrideApplied

    applied = HumanOverrideApplied(
        requested={"equity": 80.0, "debt": 15.0, "others": 5.0},
        achieved={"equity": 78.0, "debt": 17.0, "others": 5.0},
    )
    outcome = PracticalAllocationRunOutcome(result=_result(applied))
    persist = AsyncMock(return_value=uuid.uuid4())
    pref_id = uuid.uuid4()
    paa_chat, p1, p2, p3 = _patched_handle(outcome, persist)

    with p1, p2, p3:
        asyncio.run(paa_chat.handle(_ctx(_user_with_pref(pref_id))))

    assert persist.await_args.kwargs["saved_investment_preference_id"] == pref_id


def test_handle_passes_none_fk_when_no_override():
    """No override applied -> NULL, even when an active row exists (the run
    was computed neutral)."""
    from app.domains.practical_asset_allocation.services.paa_engine.service import (
        PracticalAllocationRunOutcome,
    )

    outcome = PracticalAllocationRunOutcome(result=_result(None))
    persist = AsyncMock(return_value=uuid.uuid4())
    paa_chat, p1, p2, p3 = _patched_handle(outcome, persist)

    with p1, p2, p3:
        asyncio.run(paa_chat.handle(_ctx(_user_with_pref(uuid.uuid4()))))

    assert persist.await_args.kwargs["saved_investment_preference_id"] is None
