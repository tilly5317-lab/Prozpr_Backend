"""Tests for goal_allocation_input_builder: User → AllocationInput mapping."""

import unittest
from datetime import date
from unittest.mock import MagicMock

from app.services.ai_bridge.goal_allocation_input_builder import (
    build_goal_allocation_input_for_user,
)


class ChatOverrideTests(unittest.TestCase):
    """Transient _chat_*_override attributes on User flow into AllocationInput."""

    def _build_minimal_user(self):
        """Build a minimal mock User with required attributes for allocation input."""
        user = MagicMock()
        user.date_of_birth = date(1986, 1, 1)
        user.first_name = "Tilly"
        user.investment_profile = MagicMock(
            annual_income=1_000_000.0,
            net_financial_assets=8_000_000.0,
            regular_outgoings=50_000.0,
            investable_assets=8_000_000.0,  # For _pick_total_corpus
            portfolio_value=0.0,  # For _pick_total_corpus
            primary_income_from_portfolio=False,
            intergenerational_transfer=False,
            emergency_fund=200_000.0,
        )
        user.risk_profile = MagicMock(
            effective_risk_score=5.4,
            occupation_type=None,
        )
        user.effective_risk_assessment = None
        user.tax_profile = MagicMock(
            effective_tax_rate=30.0,
            tax_regime="new",
            income_tax_rate=30.0,
        )
        user.financial_goals = []
        user.portfolios = []
        user.investment_constraints = MagicMock()
        # Explicitly set chat override attributes to None so getattr returns None
        # (rather than MagicMock's auto-created attributes)
        user._chat_risk_score_override = None
        user._chat_total_corpus_override = None
        user._chat_annual_income_override = None
        user._chat_monthly_expense_override = None
        user._chat_emergency_fund_needed_override = None
        user._chat_tax_regime_override = None
        return user

    def test_risk_score_override_already_works(self):
        """Existing _chat_risk_score_override should flow through."""
        user = self._build_minimal_user()
        user._chat_risk_score_override = 8.0
        alloc_input, _ = build_goal_allocation_input_for_user(user)
        self.assertEqual(alloc_input.effective_risk_score, 8.0)

    def test_total_corpus_override(self):
        """_chat_total_corpus_override should override total_corpus."""
        user = self._build_minimal_user()
        user._chat_total_corpus_override = 12_000_000.0
        alloc_input, _ = build_goal_allocation_input_for_user(user)
        self.assertEqual(alloc_input.total_corpus, 12_000_000.0)

    def test_annual_income_override(self):
        """_chat_annual_income_override should override annual_income."""
        user = self._build_minimal_user()
        user._chat_annual_income_override = 3_000_000.0
        alloc_input, _ = build_goal_allocation_input_for_user(user)
        self.assertEqual(alloc_input.annual_income, 3_000_000.0)

    def test_monthly_expense_override(self):
        """_chat_monthly_expense_override should override monthly_household_expense."""
        user = self._build_minimal_user()
        user._chat_monthly_expense_override = 30_000.0
        alloc_input, _ = build_goal_allocation_input_for_user(user)
        self.assertEqual(alloc_input.monthly_household_expense, 30_000.0)

    def test_emergency_fund_needed_override(self):
        """_chat_emergency_fund_needed_override should override emergency_fund_needed."""
        user = self._build_minimal_user()
        user._chat_emergency_fund_needed_override = True
        alloc_input, _ = build_goal_allocation_input_for_user(user)
        self.assertTrue(alloc_input.emergency_fund_needed)

    def test_tax_regime_override(self):
        """_chat_tax_regime_override should override tax_regime."""
        user = self._build_minimal_user()
        user._chat_tax_regime_override = "old"
        alloc_input, _ = build_goal_allocation_input_for_user(user)
        self.assertEqual(alloc_input.tax_regime, "old")

    def test_no_overrides_returns_baseline(self):
        """No _chat_*_override attributes should return baseline values."""
        user = self._build_minimal_user()
        # No _chat_*_override attributes set — baseline
        alloc_input, _ = build_goal_allocation_input_for_user(user)
        # When effective_risk_assessment is None, defaults to 7.0
        self.assertEqual(alloc_input.effective_risk_score, 7.0)
        # Default tax_regime is "new"
        self.assertEqual(alloc_input.tax_regime, "new")
        # Default emergency_fund_needed is False
        self.assertFalse(alloc_input.emergency_fund_needed)

    def test_corpus_override_propagates_into_synthesized_default_goal(self):
        """When user has no explicit goals AND a corpus override, the synthesized
        default goal's amount_needed should reflect the overridden corpus."""
        user = self._build_minimal_user()
        # No explicit goals — should trigger the synthesized default
        user.financial_goals = []
        user._chat_total_corpus_override = 5_000_000.0

        alloc_input, _ = build_goal_allocation_input_for_user(user)

        # The synthesized default goal's amount_needed should match the override
        self.assertEqual(len(alloc_input.goals), 1)
        synthesized = alloc_input.goals[0]
        self.assertEqual(synthesized.goal_name, "Long-term wealth creation")
        self.assertEqual(synthesized.amount_needed, 5_000_000.0)


if __name__ == "__main__":
    unittest.main()
