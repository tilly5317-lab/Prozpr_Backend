"""Per-module Anthropic API key wiring (audit F8).

Each AI module attributes its LLM spend to its own key (with shared fallback to
``ANTHROPIC_API_KEY``). The mechanism is EXPLICIT ``api_key`` threading into
each agent's LLM constructor(s) — ``api_key=None`` falls back to the ambient
env var. The previous mechanism (``common.anthropic_api_key_env`` mutating
``os.environ`` around calls) raced under async concurrency and was removed.

These tests assert each module passes its key explicitly and never mutates the
process environment.
"""

from __future__ import annotations

import asyncio  # noqa: F401  (asyncio_mode=auto runs the async test below)
import os
from unittest.mock import MagicMock

# Importing the app package installs the AI_Agents/src sys.path hook, so the
# bundled agents (``common``, ``risk_profiling``, ...) become importable.
import app.core.config as _cfg  # noqa: F401


# --------------------------------------------------------------------------- #
# risk_profiling — app entry threads RISK_PROFILING_API_KEY into the chain
# --------------------------------------------------------------------------- #


def test_risk_profiling_threads_its_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "SHARED")
    monkeypatch.setenv("RISK_PROFILING_API_KEY", "RISK-KEY")

    import risk_profiling

    captured: dict[str, str | None] = {}

    def _capture(payload, api_key=None):
        captured["api_key"] = api_key
        captured["env_during_call"] = os.environ.get("ANTHROPIC_API_KEY")
        return {"ok": True}

    monkeypatch.setattr(risk_profiling, "run_risk_profiling", _capture)

    from app.domains.profile.services._effective_risk.calculation import (
        EffectiveRiskComputationInput,
        compute_effective_risk_document,
    )

    compute_effective_risk_document(
        EffectiveRiskComputationInput(
            age=35,
            occupation_type="private_sector",
            annual_income=1_000_000,
            annual_expense=500_000,
            financial_assets=2_000_000,
            liabilities_excluding_mortgage=0,
            annual_mortgage_payment=0,
            properties_owned=0,
            risk_willingness=5.0,
        )
    )

    assert captured["api_key"] == "RISK-KEY"
    assert captured["env_during_call"] == "SHARED"  # env never mutated


# --------------------------------------------------------------------------- #
# market_commentary — the agent passes its key into every LLM builder
# --------------------------------------------------------------------------- #


def test_market_commentary_threads_its_key(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "SHARED")

    import market_commentary.main as mcmain

    captured: dict[str, str | None] = {}

    class _FakeLLM:
        def __init__(self, *args, **kwargs):
            captured["api_key"] = kwargs.get("api_key")
            captured["env_during_call"] = os.environ.get("ANTHROPIC_API_KEY")

        def bind_tools(self, *args, **kwargs):
            return self

        def invoke(self, *args, **kwargs):
            resp = MagicMock()
            resp.tool_calls = []  # "model did not finalise" -> empty snapshot
            return resp

    monkeypatch.setattr(mcmain, "ChatAnthropic", _FakeLLM)
    mcmain._get_extraction_llm.cache_clear()

    agent = mcmain.MarketCommentaryAgent(
        api_key="MC-KEY", output_dir=str(tmp_path), generate_document=False
    )
    agent.run()

    assert captured["api_key"] == "MC-KEY"
    assert captured["env_during_call"] == "SHARED"  # env never mutated

    mcmain._get_extraction_llm.cache_clear()  # don't leak the fake into other tests


# --------------------------------------------------------------------------- #
# goal_planning (cashflow) — NO per-module key test.
#
# The chat path no longer makes a goal-planning-specific LLM call. Its second
# Haiku call (``summarize_plan``) pre-narrated the engine output for the answer
# formatter to narrate again; it was removed. Goal-planning chat spend now bills
# to the answer-formatter key, so ANTHROPIC_GOAL_PLANNING_API_KEY has no live
# consumer. Restore a test here if a goal-planning-owned LLM call comes back.
# --------------------------------------------------------------------------- #
