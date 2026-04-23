"""Unit tests for state slimmer functions in prompts.py."""
import pytest
from src.goal_based_allocation.prompts import (
    _slim_for_step2, _slim_for_step3, _slim_for_step4,
    _slim_for_step5, _slim_for_step6, _slim_for_step7,
)


def _make_full_state() -> dict:
    return {
        "effective_risk_score": 7.0,
        "age": 35,
        "annual_income": 2_000_000,
        "osi": 0.8,
        "savings_rate_adjustment": "equity_boost",
        "gap_exceeds_3": False,
        "total_corpus": 3_000_000,
        "monthly_household_expense": 60_000,
        "tax_regime": "old",
        "section_80c_utilized": 0.0,
        "effective_tax_rate": 30.0,
        "primary_income_from_portfolio": False,
        "emergency_fund_needed": True,
        "net_financial_assets": 500_000,
        "occupation_type": "private_sector",
        "goals": [
            {"goal_name": "Car", "time_to_goal_months": 18,
             "amount_needed": 800_000, "goal_priority": "negotiable"},
        ],
        "step1_emergency": {"output": {"remaining_corpus": 2_820_000, "subgroup_amounts": {"debt_subgroup": 180_000}}},
        "step2_short_term": {"output": {"remaining_corpus": 2_020_000, "subgroup_amounts": {"debt_subgroup": 800_000}}},
        "step3_medium_term": {"output": {"remaining_corpus": 1_020_000, "subgroup_amounts": {}}},
        "step4_long_term":   {"output": {"remaining_corpus": 0, "subgroup_amounts": {}}},
        "step5_aggregation": {"output": {"rows": [], "grand_total": 3_000_000}},
        "step6_guardrails":  {"output": {"fund_mappings": [], "validation": {}}},
    }


def test_slim_for_step2_has_step1_output():
    slim = _slim_for_step2(_make_full_state())
    assert "step1_emergency" in slim
    assert slim["step1_emergency"].get("output", {}).get("remaining_corpus") == 2_820_000


def test_slim_for_step2_excludes_later_steps():
    slim = _slim_for_step2(_make_full_state())
    assert "step2_short_term" not in slim
    assert "step3_medium_term" not in slim


def test_slim_for_step3_has_step1_and_step2():
    slim = _slim_for_step3(_make_full_state())
    assert "step1_emergency" in slim
    assert "step2_short_term" in slim
    assert "step3_medium_term" not in slim


def test_slim_for_step4_has_steps_1_to_3():
    slim = _slim_for_step4(_make_full_state())
    assert "step1_emergency" in slim
    assert "step2_short_term" in slim
    assert "step3_medium_term" in slim
    assert "step4_long_term" not in slim


def test_slim_for_step5_has_all_bucket_outputs():
    slim = _slim_for_step5(_make_full_state())
    assert "step1_emergency" in slim
    assert "step2_short_term" in slim
    assert "step3_medium_term" in slim
    assert "step4_long_term" in slim
    assert "step5_aggregation" not in slim


def test_slim_for_step6_has_step4_and_step5():
    slim = _slim_for_step6(_make_full_state())
    assert "step4_long_term" in slim
    assert "step5_aggregation" in slim
    assert "effective_risk_score" in slim


def test_slim_for_step7_has_all_steps():
    slim = _slim_for_step7(_make_full_state())
    for key in ["step1_emergency", "step2_short_term", "step3_medium_term",
                "step4_long_term", "step5_aggregation", "step6_guardrails"]:
        assert key in slim
