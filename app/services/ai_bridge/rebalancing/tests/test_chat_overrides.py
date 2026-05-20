"""PR 1: rebalancing chat overrides via TurnContext.chat_overrides.

The shared `with_chat_overrides` helper is tested in asset_allocation's
test_chat_overrides.py (same code, re-imported here).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from app.models.user import User
from app.services.ai_bridge.rebalancing.overrides import (
    effective_param,
    with_chat_overrides,
)
from app.services.chat_core.turn_context import TurnContext


def _make_ctx(user: User) -> TurnContext:
    return TurnContext(
        user_ctx=user,
        user_question="x",
        conversation_history=[],
        client_context=None,
        session_id=uuid.uuid4(),
        db=None,
        effective_user_id=uuid.uuid4(),
        last_agent_runs={},
        active_intent="rebalancing",
        chat_overrides=None,
    )


def test_effective_param_returns_override_when_key_present() -> None:
    user = MagicMock(spec=User)
    ctx = with_chat_overrides(_make_ctx(user), {"effective_tax_rate": 20})
    assert effective_param(ctx, "effective_tax_rate", fallback=30) == 20


def test_effective_param_returns_fallback_when_chat_overrides_is_none() -> None:
    user = MagicMock(spec=User)
    ctx = _make_ctx(user)
    assert effective_param(ctx, "effective_tax_rate", fallback=30) == 30


def test_effective_param_returns_fallback_when_key_not_in_overrides() -> None:
    user = MagicMock(spec=User)
    ctx = with_chat_overrides(_make_ctx(user), {"stcg_offset_budget_inr": 50000})
    assert effective_param(ctx, "effective_tax_rate", fallback=30) == 30


def test_effective_param_raises_value_error_on_unknown_key() -> None:
    user = MagicMock(spec=User)
    ctx = _make_ctx(user)
    with pytest.raises(ValueError, match="unknown override key"):
        effective_param(ctx, "not_a_real_key", fallback=None)


def test_effective_param_rejects_aa_only_key() -> None:
    """AA-only keys (e.g. effective_risk_score) must NOT pass rebalancing's allow-list."""
    user = MagicMock(spec=User)
    ctx = _make_ctx(user)
    with pytest.raises(ValueError, match="unknown override key"):
        effective_param(ctx, "effective_risk_score", fallback=None)
