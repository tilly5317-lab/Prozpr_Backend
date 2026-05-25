"""Unit tests for the goal_planning input builder.

Tests the mapping from ORM models to GoalPlanningInput kwargs. Uses
lightweight stand-in objects rather than the ORM + a real DB to test
the pure mapping logic in isolation. The async ``_ensure_assumptions``
path (which hits the DB) is stubbed.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai_bridge.goal_planning.input_builder import (
    _build_assumptions_dict,
    _build_client_profile,
    _build_current_properties,
    _build_custom_goals,
    _build_goal_properties,
    _build_one_off_events,
    _build_retirement_input,
)


def _pfp(*, income=2_500_000, tax_rate=0.25, assets=10_000_000,
         liabilities=500_000, expense=80_000, sip=40_000):
    return SimpleNamespace(
        annual_income=income,
        effective_tax_rate=tax_rate,
        financial_assets=assets,
        financial_liabilities_excl_mortgage=liabilities,
        monthly_household_expense=expense,
        starting_monthly_investment=sip,
    )


def _goal_cashflow(
    *, name="Test Goal", goal_type_cashflow=None, goal_date=None,
    goal_value_pv=None, goal_value_fv=None, target_pv=None, target_fv=None,
    is_downpayment_only=False, upfront_amount=None, downpayment_pct=None,
    inflation_annual=None, mortgage_tenure_years=None,
    mortgage_interest_annual=None, date_of_birth=None,
    goal_name=None, present_value_amount=None,
):
    return SimpleNamespace(
        name=name,
        goal_name=goal_name or name,
        goal_type_cashflow=SimpleNamespace(value=goal_type_cashflow) if goal_type_cashflow else None,
        goal_date=goal_date,
        goal_value_pv=goal_value_pv,
        goal_value_fv=goal_value_fv,
        target_pv=target_pv,
        target_fv=target_fv,
        is_downpayment_only=is_downpayment_only,
        upfront_amount=upfront_amount,
        downpayment_pct=downpayment_pct,
        inflation_annual=inflation_annual,
        mortgage_tenure_years=mortgage_tenure_years,
        mortgage_interest_annual=mortgage_interest_annual,
        date_of_birth=date_of_birth,
        present_value_amount=present_value_amount,
    )


class TestBuildClientProfile:
    def test_happy_path(self):
        result = _build_client_profile(_pfp())
        assert result is not None
        assert result["annual_income"] == 2_500_000
        assert result["effective_tax_rate"] == 0.25
        assert result["financial_assets"] == 10_000_000
        assert result["starting_monthly_investment"] == 40_000

    def test_none_pfp_returns_none(self):
        assert _build_client_profile(None) is None

    def test_zero_income_returns_none(self):
        assert _build_client_profile(_pfp(income=0)) is None


class TestBuildRetirementInput:
    def test_from_retirement_goal(self):
        goals = [_goal_cashflow(
            goal_type_cashflow="retirement",
            date_of_birth=date(1985, 6, 15),
            goal_date=date(2047, 6, 15),
        )]
        user = SimpleNamespace(date_of_birth=date(1985, 6, 15), assumed_lifespan_years=85)
        result = _build_retirement_input(goals, user)
        assert result is not None
        assert result["date_of_birth"] == date(1985, 6, 15)
        assert result["retirement_date_override"] == date(2047, 6, 15)

    def test_fallback_to_user_dob(self):
        user = SimpleNamespace(date_of_birth=date(1990, 3, 20), assumed_lifespan_years=None)
        result = _build_retirement_input([], user)
        assert result is not None
        assert result["date_of_birth"] == date(1990, 3, 20)
        assert result["assumed_lifespan_years"] == 85
        assert result["retirement_age"] == 60

    def test_missing_dob_returns_none(self):
        user = SimpleNamespace(date_of_birth=None, assumed_lifespan_years=None)
        assert _build_retirement_input([], user) is None


class TestBuildGoalProperties:
    def test_maps_property_goal(self):
        goals = [_goal_cashflow(
            goal_type_cashflow="property",
            name="Dream Home",
            goal_date=date(2032, 4, 1),
            target_pv=15_000_000,
            is_downpayment_only=True,
            downpayment_pct=0.2,
        )]
        result = _build_goal_properties(goals)
        assert len(result) == 1
        assert result[0]["name"] == "Dream Home"
        assert result[0]["target_pv"] == 15_000_000
        assert result[0]["is_downpayment_only"] is True
        assert result[0]["downpayment_pct"] == 0.2

    def test_skips_non_property_goals(self):
        goals = [_goal_cashflow(goal_type_cashflow="custom", goal_date=date(2030, 1, 1))]
        assert _build_goal_properties(goals) == []


class TestBuildCustomGoals:
    def test_maps_custom_goal(self):
        goals = [_goal_cashflow(
            goal_type_cashflow="child_abroad_education",
            name="Kid College",
            goal_date=date(2038, 7, 1),
            goal_value_pv=5_000_000,
            inflation_annual=0.08,
        )]
        result = _build_custom_goals(goals)
        assert len(result) == 1
        assert result[0]["name"] == "Kid College"
        assert result[0]["goal_type"] == "child_abroad_education"
        assert result[0]["goal_value_pv"] == 5_000_000
        assert result[0]["inflation_rate_override"] == 0.08

    def test_falls_back_to_present_value_amount(self):
        goals = [_goal_cashflow(
            goal_type_cashflow="custom",
            name="Vacation",
            goal_date=date(2029, 12, 1),
            present_value_amount=500_000,
        )]
        result = _build_custom_goals(goals)
        assert len(result) == 1
        assert result[0]["goal_value_pv"] == 500_000


class TestBuildCurrentProperties:
    def test_maps_property_with_mortgage(self):
        props = [SimpleNamespace(
            name="Flat", property_value=8_000_000,
            has_mortgage=True, mortgage_emi=45_000,
            mortgage_end_date=date(2035, 3, 31),
        )]
        result = _build_current_properties(props)
        assert len(result) == 1
        assert result[0]["name"] == "Flat"
        assert result[0]["has_mortgage"] is True
        assert result[0]["mortgage_emi"] == 45_000

    def test_maps_property_without_mortgage(self):
        props = [SimpleNamespace(
            name="Land", property_value=3_000_000,
            has_mortgage=False, mortgage_emi=None,
            mortgage_end_date=None,
        )]
        result = _build_current_properties(props)
        assert result[0]["has_mortgage"] is False
        assert "mortgage_emi" not in result[0]


class TestBuildOneOffEvents:
    def test_splits_inflows_and_outflows(self):
        events = [
            SimpleNamespace(
                description="Bonus", amount=500_000,
                event_date=date(2027, 3, 31),
                direction=SimpleNamespace(value="in"),
            ),
            SimpleNamespace(
                description="Wedding", amount=2_000_000,
                event_date=date(2028, 11, 15),
                direction=SimpleNamespace(value="out"),
            ),
        ]
        inflows, outflows = _build_one_off_events(events)
        assert len(inflows) == 1
        assert inflows[0]["description"] == "Bonus"
        assert len(outflows) == 1
        assert outflows[0]["description"] == "Wedding"
