"""Tests for goal_allocation_input_builder: User → AllocationInput mapping."""

import unittest
import uuid
from datetime import date
from unittest.mock import MagicMock

from app.domains.asset_allocation.services.aa_engine.input_builder import (
    build_goal_allocation_input_for_user,
)
from app.domains.asset_allocation.services.aa_engine.overrides import (
    with_chat_overrides,
)
from app.domains.ai_engine.turn_context import TurnContext


class ChatOverrideTests(unittest.TestCase):
    """TurnContext.chat_overrides flow into AllocationInput via the input builder."""

    def _build_minimal_user(self):
        """Build a minimal mock User with required attributes for allocation input."""
        user = MagicMock()
        user.date_of_birth = date(1986, 1, 1)
        user.first_name = "Tilly"
        # Canonical household-finance scalars live on personal_finance_profiles.
        user.personal_finance_profile = MagicMock(
            annual_income=1_000_000.0,
            monthly_household_expense=50_000.0,
            financial_assets=8_000_000.0,  # For pick_total_corpus
            financial_liabilities_excl_mortgage=0.0,
            starting_monthly_investment=None,
        )
        user.investment_profile = MagicMock(
            portfolio_value=0.0,  # For pick_total_corpus
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
        return user

    def _make_ctx(self, user, **overrides) -> TurnContext:
        ctx = TurnContext(
            user_ctx=user,
            user_question="x",
            conversation_history=[],
            client_context=None,
            session_id=uuid.uuid4(),
            db=None,
            effective_user_id=uuid.uuid4(),
            last_agent_runs={},
            active_intent="asset_allocation",
            chat_overrides=None,
        )
        return with_chat_overrides(ctx, overrides) if overrides else ctx

    def test_risk_score_override_already_works(self):
        """Risk-score override flows from chat_overrides into AllocationInput."""
        user = self._build_minimal_user()
        ctx = self._make_ctx(user, effective_risk_score=8.0)
        alloc_input, _ = build_goal_allocation_input_for_user(ctx)
        self.assertEqual(alloc_input.effective_risk_score, 8.0)

    def test_total_corpus_override(self):
        """total_corpus override flows from chat_overrides."""
        user = self._build_minimal_user()
        ctx = self._make_ctx(user, total_corpus=12_000_000.0)
        alloc_input, _ = build_goal_allocation_input_for_user(ctx)
        self.assertEqual(alloc_input.total_corpus, 12_000_000.0)

    def test_additional_cash_override_adds_to_baseline(self):
        """additional_cash_inr ADDS to the baseline corpus.

        Baseline (from minimal user fixture) is 8_000_000; +200_000 → 8_200_000.
        """
        user = self._build_minimal_user()
        ctx = self._make_ctx(user, additional_cash_inr=200_000.0)
        alloc_input, _ = build_goal_allocation_input_for_user(ctx)
        self.assertEqual(alloc_input.total_corpus, 8_200_000.0)

    def test_additional_cash_override_stacks_with_total_corpus_override(self):
        """additional_cash adds on top of an absolute total_corpus override."""
        user = self._build_minimal_user()
        ctx = self._make_ctx(
            user,
            total_corpus=5_000_000.0,
            additional_cash_inr=200_000.0,
        )
        alloc_input, _ = build_goal_allocation_input_for_user(ctx)
        self.assertEqual(alloc_input.total_corpus, 5_200_000.0)

    def test_annual_income_override(self):
        """annual_income override flows from chat_overrides."""
        user = self._build_minimal_user()
        ctx = self._make_ctx(user, annual_income=3_000_000.0)
        alloc_input, _ = build_goal_allocation_input_for_user(ctx)
        self.assertEqual(alloc_input.annual_income, 3_000_000.0)

    def test_monthly_expense_override(self):
        """monthly_household_expense override flows from chat_overrides."""
        user = self._build_minimal_user()
        ctx = self._make_ctx(user, monthly_household_expense=30_000.0)
        alloc_input, _ = build_goal_allocation_input_for_user(ctx)
        self.assertEqual(alloc_input.monthly_household_expense, 30_000.0)

    def test_emergency_fund_needed_override(self):
        """emergency_fund_needed override flows from chat_overrides."""
        user = self._build_minimal_user()
        ctx = self._make_ctx(user, emergency_fund_needed=True)
        alloc_input, _ = build_goal_allocation_input_for_user(ctx)
        self.assertTrue(alloc_input.emergency_fund_needed)

    def test_tax_regime_override(self):
        """tax_regime override flows from chat_overrides."""
        user = self._build_minimal_user()
        ctx = self._make_ctx(user, tax_regime="old")
        alloc_input, _ = build_goal_allocation_input_for_user(ctx)
        self.assertEqual(alloc_input.tax_regime, "old")

    def test_no_overrides_returns_baseline(self):
        """No chat_overrides → baseline values from the User."""
        user = self._build_minimal_user()
        ctx = self._make_ctx(user)
        alloc_input, _ = build_goal_allocation_input_for_user(ctx)
        # When effective_risk_assessment is None, defaults to 7.0
        self.assertEqual(alloc_input.effective_risk_score, 7.0)
        # Default tax_regime is "new"
        self.assertEqual(alloc_input.tax_regime, "new")
        # Default emergency_fund_needed is False
        self.assertFalse(alloc_input.emergency_fund_needed)

    def test_no_goals_yields_empty_goals_list(self):
        """Users with no active financial goals get an empty goals list.

        The engine's ``AllocationInput.goals`` defaults to ``[]`` (see
        ``AI_Agents/src/asset_allocation_pydantic/models.py``); the input
        builder used to synthesize a long-term wealth-creation goal as a
        stand-in but that masked real "user hasn't onboarded" cases. Now we
        just pass through, the engine handles it, and the debug dict
        surfaces ``goals_empty`` so we can observe it.
        """
        user = self._build_minimal_user()
        user.financial_goals = []
        ctx = self._make_ctx(user, total_corpus=5_000_000.0)

        alloc_input, debug = build_goal_allocation_input_for_user(ctx)

        self.assertEqual(alloc_input.goals, [])
        self.assertEqual(debug["active_goal_count"], 0)
        self.assertIn("goals_empty", debug["defaults_applied"])

    def test_missing_date_of_birth_defaults_to_age_35(self):
        """No DOB on file → age defaults to 35; the engine still runs."""
        user = self._build_minimal_user()
        user.date_of_birth = None
        ctx = self._make_ctx(user)

        alloc_input, debug = build_goal_allocation_input_for_user(ctx)

        self.assertEqual(alloc_input.age, 35)
        self.assertFalse(debug["has_date_of_birth"])
        self.assertIn("date_of_birth_missing", debug["defaults_applied"])


if __name__ == "__main__":
    unittest.main()
