"""LIVE eval gate for the AA / rebalancing / goal-planning / additional-investment
action detectors (audit F10).

Run BEFORE merging changes to any detector prompt: ``aa_engine/chat.py``'s
``_DETECT_SYSTEM``, ``rebal_engine/chat.py``'s ``_DETECT_REBAL_SYSTEM``,
``goal_planning_engine/chat.py``'s ``_DETECT_SYSTEM``, or
``ainv_engine/chat.py``'s ``_DEPLOY_EXTRACT_SYSTEM``:

    ENABLE_LLM_SMOKE=1 .venv-mac/bin/python -m pytest -m real_llm \
        app/domains/ai_engine/tests/eval_gate/ -s

Exercises the PRODUCTION detector for each module. AA and rebalancing read a
pinned AgentRun snapshot; goal-planning reads the plan off a mocked user_ctx;
additional-investment's extractor takes only (question, history) and grades the
(amount, cadence, category, preference_asks) form it fills. ~49 Haiku calls. Not
run in CI.
"""

from __future__ import annotations

import asyncio
import os
import unittest
import uuid
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.real_llm

if os.getenv("ENABLE_LLM_SMOKE") != "1":
    pytest.skip(
        "Set ENABLE_LLM_SMOKE=1 to run the live prompt eval gate",
        allow_module_level=True,
    )

import app.all_models  # noqa: F401
import app.domains.ai_engine  # noqa: F401
from app.domains.ai_engine.turn_context import AgentRunRecord, TurnContext
from AI_Agents.tests._eval_harness import run_suite

from .golden_cases import (
    AA_DETECT_CASES,
    AA_DETECT_THRESHOLD,
    REBAL_DETECT_CASES,
    REBAL_DETECT_THRESHOLD,
    GOAL_DETECT_CASES,
    GOAL_DETECT_THRESHOLD,
    AINV_DEPLOY_CASES,
    AINV_DEPLOY_THRESHOLD,
    DetectCase,
    DeployCase,
)

# Pinned snapshots: small but realistic — the detectors' slim/digest builders
# read these exact shapes.
_AA_SNAPSHOT = {
    "allocation_result": {
        "client_summary": {"effective_risk_score": 5.5, "age": 40},
        "grand_total": 8_000_000,
        "asset_class_breakdown": {
            "actual": {
                "equity_total_pct": 60,
                "debt_total_pct": 30,
                "others_total_pct": 10,
            }
        },
        "bucket_allocations": [
            {
                "bucket": "long_term",
                "total_goal_amount": 5_000_000,
                "allocated_amount": 4_000_000,
                "goals": [
                    {
                        "goal_name": "Retirement",
                        "amount_needed": 5_000_000,
                        "time_to_goal_months": 240,
                    }
                ],
                "future_investment": None,
            }
        ],
    }
}

_REBAL_SNAPSHOT = {
    "rebalancing_response": {
        "trade_count": 4,
        "fund_actions": [
            {"fund_name": "HDFC Top 100", "sub_category": "Large Cap Fund", "sell_inr": 50_000},
            {"fund_name": "Mirae Asset Large Cap", "sub_category": "Large Cap Fund", "buy_inr": 50_000},
        ],
        "buckets": [
            {"sub_category": "Large Cap Fund"},
            {"sub_category": "Liquid Fund"},
        ],
    }
}


def _agent_run(module: str, payload: dict) -> AgentRunRecord:
    return AgentRunRecord(
        id=uuid.uuid4(),
        module=module,
        intent_detected=module,
        input_payload=None,
        output_payload=payload,
        created_at=datetime.now(),
    )


def _ctx(
    question: str,
    module: str,
    payload: dict,
    history: tuple[tuple[str, str], ...] = (),
) -> TurnContext:
    return TurnContext(
        user_ctx=MagicMock(first_name="Tilly"),
        user_question=question,
        conversation_history=[{"role": r, "content": c} for r, c in history],
        client_context=None,
        session_id=uuid.uuid4(),
        db=None,
        effective_user_id=uuid.uuid4(),
        last_agent_runs={module: _agent_run(module, payload)},
        active_intent=module,
    )


def _grade(case: DetectCase, action) -> tuple[bool, str]:
    if action is None:
        return False, "detector returned None"
    if action.mode not in case.expected_modes:
        return False, f"expected mode {sorted(case.expected_modes)}, got {action.mode!r}"
    if case.expected_override_keys:
        got_keys = set((action.overrides or {}).keys())
        if not case.expected_override_keys <= got_keys:
            return (
                False,
                f"expected override keys ⊇ {sorted(case.expected_override_keys)}, got {sorted(got_keys)}",
            )
    return True, ""


def _norm_cat(c: str | None) -> str | None:
    """Lower-case, strip non-alphanumerics — 'small cap' == 'smallcap' == 'small-cap'."""
    if not c:
        return None
    return "".join(ch for ch in c.lower() if ch.isalnum()) or None


def _deploy_grade(case: DeployCase, result) -> tuple[bool, str]:
    if result is None:
        return False, "extractor returned None"
    amount, cadence, category, asks = result
    if amount != case.expected_amount:
        return False, f"amount: expected {case.expected_amount}, got {amount}"
    cad = getattr(cadence, "value", cadence)
    if cad != case.expected_cadence:
        return False, f"cadence: expected {case.expected_cadence}, got {cad}"
    exp, got = _norm_cat(case.expected_category), _norm_cat(category)
    if exp is None:
        if got is not None:
            return False, f"category: expected null, got {category!r}"
    elif got is None or (exp not in got and got not in exp):
        return False, f"category: expected ~{case.expected_category!r}, got {category!r}"
    # A category ask is a question about PICKS, not a lean — the boundary the
    # S2c prompt draws. Both firing on one question routes a plain deploy into
    # the what-if handler.
    if case.expected_category is not None and asks:
        return (
            False,
            "focus-category case must not emit preference_asks, got "
            f"{[a.target for a in asks]}",
        )
    if case.expected_asks_target is not None:
        if not asks:
            return False, f"expected preference_asks[0].target={case.expected_asks_target!r}, got none"
        if asks[0].target != case.expected_asks_target:
            return (
                False,
                f"asks[0].target: expected {case.expected_asks_target!r}, got {asks[0].target!r}",
            )
    return True, ""


class AaDetectEvalGate(unittest.TestCase):
    def test_aa_detect_golden_set(self) -> None:
        from app.domains.asset_allocation.services.aa_engine import chat as aa_chat

        def run_case(case: DetectCase):
            ctx = _ctx(case.question, "asset_allocation", _AA_SNAPSHOT)
            return asyncio.run(aa_chat._speculative_detect(ctx))

        report = run_suite(
            suite="aa_action_detect_golden",
            cases=AA_DETECT_CASES,
            runner=run_case,
            grader=_grade,
            threshold=AA_DETECT_THRESHOLD,
        )
        report.assert_threshold(self)


class RebalDetectEvalGate(unittest.TestCase):
    def test_rebal_detect_golden_set(self) -> None:
        from app.domains.rebalancing.services.rebal_engine import chat as rb_chat

        def run_case(case: DetectCase):
            ctx = _ctx(case.question, "rebalancing", _REBAL_SNAPSHOT, case.history)
            return asyncio.run(rb_chat._speculative_detect(ctx))

        report = run_suite(
            suite="rebal_action_detect_golden",
            cases=REBAL_DETECT_CASES,
            runner=run_case,
            grader=_grade,
            threshold=REBAL_DETECT_THRESHOLD,
        )
        report.assert_threshold(self)


class GoalDetectEvalGate(unittest.TestCase):
    """Goal-planning detector reads the plan from user_ctx (not an AgentRun snapshot),
    so the profile is mocked: retires at 60, expenses ₹2L/mo, SIP ₹50k/mo. A named age
    other than 60 is a change (counterfactual); 60 or no age is narrate."""

    def test_goal_detect_golden_set(self) -> None:
        from app.domains.cashflow.services.goal_planning_engine import chat as gp_chat

        def _goal_ctx(case: DetectCase) -> TurnContext:
            user_ctx = MagicMock(first_name="Tilly")
            user_ctx.investment_profile.retirement_age = 60
            return TurnContext(
                user_ctx=user_ctx,
                user_question=case.question,
                conversation_history=[
                    {"role": r, "content": c} for r, c in case.history
                ],
                client_context=None,
                session_id=uuid.uuid4(),
                db=None,
                effective_user_id=uuid.uuid4(),
                last_agent_runs={},
                active_intent="goal_planning",
            )

        def run_case(case: DetectCase):
            ctx = _goal_ctx(case)
            with patch(
                "app.domains.profile.services.profile_finance.personal_finance_scalars",
                return_value={
                    "monthly_household_expense": 200_000,
                    "starting_monthly_investment": 50_000,
                },
            ):
                return asyncio.run(gp_chat._detect_goal_action(ctx))

        report = run_suite(
            suite="goal_action_detect_golden",
            cases=GOAL_DETECT_CASES,
            runner=run_case,
            grader=_grade,
            threshold=GOAL_DETECT_THRESHOLD,
        )
        report.assert_threshold(self)


class AinvDeployEvalGate(unittest.TestCase):
    """Additional-investment extractor: grades the (amount, cadence, category,
    preference_asks) form it fills, not a chosen mode. Takes only (question,
    history) — no profile needed."""

    def test_ainv_deploy_golden_set(self) -> None:
        from app.domains.additional_investment.services.ainv_engine import chat as ainv_chat

        def run_case(case: DeployCase):
            history = [{"role": r, "content": c} for r, c in case.history] or None
            return asyncio.run(ainv_chat.extract_deploy_request(case.question, history))

        report = run_suite(
            suite="ainv_deploy_extract_golden",
            cases=AINV_DEPLOY_CASES,
            runner=run_case,
            grader=_deploy_grade,
            threshold=AINV_DEPLOY_THRESHOLD,
        )
        report.assert_threshold(self)


if __name__ == "__main__":
    unittest.main()
