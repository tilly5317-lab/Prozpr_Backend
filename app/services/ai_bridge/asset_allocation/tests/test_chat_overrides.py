"""PR 1 regression: counterfactual override must NOT mutate the User instance.

The legacy code (asset_allocation/chat.py:578-594) called setattr on the
SQLAlchemy User with seven _chat_*_override attributes, then deleted them in a
finally block. If anything between set and clear flushed the session or raised
unexpectedly, the chat-only state could leak onto the persisted user row.

The fix: thread overrides through the frozen TurnContext via
with_chat_overrides(ctx, overrides). The User instance is never mutated.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from app.models.user import User
from app.services.ai_bridge.asset_allocation.overrides import (
    effective_param,
    with_chat_overrides,
)
from app.services.chat_core.turn_context import TurnContext


_OVERRIDE_USER_ATTRS = (
    "_chat_risk_score_override",
    "_chat_total_corpus_override",
    "_chat_additional_cash_override",
    "_chat_annual_income_override",
    "_chat_monthly_expense_override",
    "_chat_emergency_fund_needed_override",
    "_chat_tax_regime_override",
)


def _make_ctx(user: User) -> TurnContext:
    return TurnContext(
        user_ctx=user,
        user_question="what if my risk were 7?",
        conversation_history=[],
        client_context=None,
        session_id=uuid.uuid4(),
        db=None,
        effective_user_id=uuid.uuid4(),
        last_agent_runs={},
        active_intent="asset_allocation",
        chat_overrides=None,
    )


def test_with_chat_overrides_threads_via_ctx_without_mutating_user() -> None:
    user = MagicMock(spec=User)
    ctx = _make_ctx(user)

    for attr in _OVERRIDE_USER_ATTRS:
        assert not hasattr(user, attr), f"User starts with stale {attr}"

    new_ctx = with_chat_overrides(ctx, {"effective_risk_score": 7})

    for attr in _OVERRIDE_USER_ATTRS:
        assert not hasattr(user, attr), f"PR 1 regression: User mutated with {attr}"

    assert new_ctx.chat_overrides == {"effective_risk_score": 7}
    assert ctx.chat_overrides is None


def test_with_chat_overrides_normalizes_falsy_to_none() -> None:
    user = MagicMock(spec=User)
    ctx = _make_ctx(user)
    assert with_chat_overrides(ctx, {}).chat_overrides is None
    assert with_chat_overrides(ctx, None).chat_overrides is None


def test_effective_param_returns_override_when_key_present() -> None:
    user = MagicMock(spec=User)
    ctx = with_chat_overrides(_make_ctx(user), {"effective_risk_score": 7})
    assert effective_param(ctx, "effective_risk_score", fallback=5) == 7


def test_effective_param_returns_fallback_when_chat_overrides_is_none() -> None:
    user = MagicMock(spec=User)
    ctx = _make_ctx(user)
    assert effective_param(ctx, "effective_risk_score", fallback=5) == 5


def test_effective_param_returns_fallback_when_key_not_in_overrides() -> None:
    user = MagicMock(spec=User)
    ctx = with_chat_overrides(_make_ctx(user), {"total_corpus": 9_000_000})
    assert effective_param(ctx, "effective_risk_score", fallback=5) == 5


def test_effective_param_raises_value_error_on_unknown_key() -> None:
    user = MagicMock(spec=User)
    ctx = _make_ctx(user)
    with pytest.raises(ValueError, match="unknown override key"):
        effective_param(ctx, "not_a_real_key", fallback=None)
