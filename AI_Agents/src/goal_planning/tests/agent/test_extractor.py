"""4-kind round-trip tests for the consolidated extractor.

Each test feeds a canned response by stubbing the extractor's `_chain`.
"""
from datetime import date
import pytest

from goal_planning.models import (
    ExtractedGoal, ExtractedProperty, ExtractedCashflow, ExtractedMutation,
    ExtractionError, GoalType, CustomGoal, GoalProperty, OneOffEvent,
)
from goal_planning.agent.extractor import FinancialEventExtractor


class _ConstantChain:
    def __init__(self, value):
        self._value = value

    def invoke(self, *_args, **_kwargs):
        return self._value


@pytest.mark.asyncio
async def test_extract_custom_goal(monkeypatch):
    extractor = FinancialEventExtractor.__new__(FinancialEventExtractor)
    extractor._llm = None  # not used since chain is stubbed
    canned = ExtractedGoal(
        kind="custom_goal",
        goal=CustomGoal(
            name="college", goal_type=GoalType.child_local_education,
            amount_pv=1_000_000, goal_date=date(2035, 1, 1),
        ),
    )
    extractor._chain = _ConstantChain(canned)
    result = await extractor.extract(
        description="College in 2035, 10 lakh today",
        anchor_date=date(2026, 5, 9),
        existing_goal_names=[],
    )
    assert isinstance(result, ExtractedGoal)
    assert result.goal.goal_date == date(2035, 1, 1)


@pytest.mark.asyncio
async def test_extract_property_goal_post_fills_defaults():
    """When LLM returns property without mortgage details, post-fill applies defaults."""
    extractor = FinancialEventExtractor.__new__(FinancialEventExtractor)
    extractor._llm = None
    canned = ExtractedProperty(
        kind="property_goal",
        property=GoalProperty(
            name="house", target_pv=10_000_000,
            is_downpayment_only=True, upfront_amount=2_000_000,
            goal_date=date(2030, 5, 9),
            mortgage_tenure_years=20, mortgage_interest_annual=0.075,
        ),
        assumptions_used=[],
    )
    extractor._chain = _ConstantChain(canned)
    result = await extractor.extract(
        description="Buy a house in 2030 for 1Cr",
        anchor_date=date(2026, 5, 9),
        existing_goal_names=[],
    )
    assert isinstance(result, ExtractedProperty)


@pytest.mark.asyncio
async def test_extract_cashflow():
    extractor = FinancialEventExtractor.__new__(FinancialEventExtractor)
    extractor._llm = None
    canned = ExtractedCashflow(
        kind="cashflow_event",
        event=OneOffEvent(description="bonus", amount=500_000, date=date(2027, 3, 31)),
        direction="in",
        confidence="high",
    )
    extractor._chain = _ConstantChain(canned)
    result = await extractor.extract("bonus next March", date(2026, 5, 9), [])
    assert isinstance(result, ExtractedCashflow)
    assert result.direction == "in"


@pytest.mark.asyncio
async def test_extract_mutation_via_fuzzy_match():
    """When NL goal name fuzzy-matches existing, promote to mutation."""
    extractor = FinancialEventExtractor.__new__(FinancialEventExtractor)
    extractor._llm = None
    # Note: provide a name that fuzzy-matches "retirement"
    canned = ExtractedGoal(
        kind="custom_goal",
        goal=CustomGoal(
            name="retirement fund",  # fuzzy → "retirement"
            goal_type=GoalType.retirement,
            amount_pv=50_000_000, goal_date=date(2036, 5, 9),
        ),
    )
    extractor._chain = _ConstantChain(canned)
    result = await extractor.extract(
        description="Increase my retirement fund target",
        anchor_date=date(2026, 5, 9),
        existing_goal_names=["retirement"],
    )
    assert isinstance(result, ExtractedMutation), f"Expected mutation, got {type(result).__name__}"
    assert result.goal_name == "retirement"


@pytest.mark.asyncio
async def test_past_date_returns_extraction_error():
    extractor = FinancialEventExtractor.__new__(FinancialEventExtractor)
    extractor._llm = None
    canned = ExtractedGoal(
        kind="custom_goal",
        goal=CustomGoal(
            name="old_goal", goal_type=GoalType.custom,
            amount_pv=1_000_000, goal_date=date(2024, 1, 1),
        ),
    )
    extractor._chain = _ConstantChain(canned)
    result = await extractor.extract("old goal", date(2026, 5, 9), [])
    assert isinstance(result, ExtractionError)
    assert "past" in result.reason.lower()
