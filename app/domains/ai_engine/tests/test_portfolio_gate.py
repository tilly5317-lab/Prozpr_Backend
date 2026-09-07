"""portfolio_gate: which intents short-circuit for a user with no holdings.

The no-CAMS cohort (2026-08-09) can plan fresh money (SIP / lump sum) without any
existing holdings, so ``additional_investment`` must NOT be portfolio-gated —
while ``rebalancing`` and ``portfolio_query``, which genuinely read holdings, stay
gated.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.domains.ai_engine import portfolio_gate as pg
from app.domains.ai_engine.portfolio_gate import (
    PORTFOLIO_REQUIRED_INTENTS,
    portfolio_data_missing,
)


def test_additional_investment_is_not_portfolio_gated():
    """Fresh-money planning needs no existing holdings."""
    assert "additional_investment" not in PORTFOLIO_REQUIRED_INTENTS


def test_holdings_intents_remain_gated():
    assert "rebalancing" in PORTFOLIO_REQUIRED_INTENTS
    assert "portfolio_query" in PORTFOLIO_REQUIRED_INTENTS


@pytest.mark.asyncio
async def test_additional_investment_runs_even_without_holdings():
    """Even with zero holdings, the fresh-money intent runs its flow (never gated)."""
    with patch.object(pg, "has_mf_holdings", new=AsyncMock(return_value=False)):
        missing = await portfolio_data_missing(
            object(), uuid.uuid4(), "additional_investment"
        )
    assert missing is False


@pytest.mark.asyncio
async def test_rebalancing_still_short_circuits_without_holdings():
    with patch.object(pg, "has_mf_holdings", new=AsyncMock(return_value=False)):
        missing = await portfolio_data_missing(object(), uuid.uuid4(), "rebalancing")
    assert missing is True


@pytest.mark.asyncio
async def test_cams_user_is_never_gated_on_any_intent():
    """A user WITH holdings (CAMS uploaded) is not gated on any intent — the gate
    changes must not affect the funded cohort."""
    with patch.object(pg, "has_mf_holdings", new=AsyncMock(return_value=True)):
        for intent in ("additional_investment", "rebalancing", "portfolio_query"):
            missing = await portfolio_data_missing(object(), uuid.uuid4(), intent)
            assert missing is False, f"{intent} wrongly gated for a holdings user"
