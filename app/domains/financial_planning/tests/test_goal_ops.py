"""Matching what the customer called a goal to a row.

Customers do not have ids; they say "the car" and "that Europe trip". The one
outcome worse than failing to match is matching the WRONG goal, so an ambiguous
reference has to come back as "which one?" rather than as a pick.
"""

from __future__ import annotations

import unittest
import uuid

from app.domains.financial_planning.services import goal_ops


class _Goal:
    """Just enough of a FinancialGoal for reference matching."""

    def __init__(self, name: str):
        self.id = uuid.uuid4()
        self.display_name = name


class TestResolveRef(unittest.TestCase):
    def setUp(self):
        self.goals = [_Goal("Thar 4x4"), _Goal("Europe trip"), _Goal("Retirement")]

    def test_exact_name(self):
        self.assertIs(goal_ops.resolve_ref(self.goals, "Europe trip"), self.goals[1])

    def test_case_insensitive(self):
        self.assertIs(goal_ops.resolve_ref(self.goals, "europe TRIP"), self.goals[1])

    def test_the_customers_words_contain_the_name(self):
        self.assertIs(
            goal_ops.resolve_ref(self.goals, "that Europe trip goal"), self.goals[1]
        )

    def test_a_fragment_of_the_name(self):
        self.assertIs(goal_ops.resolve_ref(self.goals, "Thar"), self.goals[0])

    def test_no_reference_with_exactly_one_goal_is_unambiguous(self):
        self.assertIs(goal_ops.resolve_ref([self.goals[0]], None), self.goals[0])

    def test_no_reference_with_several_goals_is_a_question(self):
        self.assertIsNone(goal_ops.resolve_ref(self.goals, None))

    def test_an_ambiguous_fragment_is_a_question_not_a_pick(self):
        goals = [_Goal("Europe trip 2030"), _Goal("Europe honeymoon")]
        self.assertIsNone(goal_ops.resolve_ref(goals, "Europe"))

    def test_an_exact_name_wins_over_a_longer_one_containing_it(self):
        # "Europe trip" names one of these exactly, so it is not ambiguous —
        # the customer used the goal's own name.
        goals = [_Goal("Europe trip"), _Goal("Europe trip 2030")]
        self.assertIs(goal_ops.resolve_ref(goals, "Europe trip"), goals[0])

    def test_an_unrecognised_reference_matches_nothing(self):
        self.assertIsNone(goal_ops.resolve_ref(self.goals, "the boat"))

    def test_empty_reference(self):
        self.assertIsNone(goal_ops.resolve_ref(self.goals, "   "))


class TestSlotsFromGoal(unittest.TestCase):
    """Editing a goal must not re-inflate a cost that is already inflated."""

    def test_cost_is_deflated_back_to_todays_money(self):
        from datetime import date, timedelta

        from app.domains.financial_planning.services import goal_builder

        class _Existing:
            id = uuid.uuid4()
            display_name = "Thar 4x4"
            goal_date = date.today() + timedelta(days=round(5 * 365.25))
            target_date = goal_date
            is_downpayment_only = False
            inflation_rate = 6.0
            # 18 lakh today at 6% for 5 years.
            goal_value_fv = 18_00_000 * (1.06**5)
            target_pv = None
            upfront_amount = None
            mortgage_interest_annual = None
            mortgage_tenure_years = None

        slots = goal_builder.slots_from_goal(_Existing())
        self.assertEqual(slots["editing_goal_id"], str(_Existing.id))
        self.assertAlmostEqual(slots["cost_pv"], 18_00_000, delta=2_000)
        self.assertAlmostEqual(slots["years"], 5.0, places=1)


if __name__ == "__main__":
    unittest.main()
