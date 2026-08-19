"""The arithmetic the model is never asked to do.

These are the cases that made the split necessary in the first place: a model
asked to annualise "2.4 lakh a month" returned a figure a crore out at 0.95
confidence. Everything here runs without an LLM, because that is the point.
"""

from __future__ import annotations

import unittest

from app.domains.financial_planning.services.operations import (
    AmbiguousUnit,
    NoBaseline,
    RelativeChange,
    apply_relative,
    scale,
    to_stored_value,
)
from app.domains.profile.services.profile_field_registry import spec

INCOME = spec("annual_income")  # stored inr_per_year
EXPENSE = spec("monthly_household_expense")  # stored inr_per_month
ASSETS = spec("financial_assets")  # stored inr, no period
RETIREMENT_AGE = spec("retirement_age")  # integer, years
SLAB = spec("income_tax_rate")  # percent, max 45


class TestScaling(unittest.TestCase):
    def test_indian_magnitude_words(self):
        self.assertEqual(scale(2.4, "lakh"), 240_000)
        self.assertEqual(scale(1.5, "crore"), 15_000_000)
        self.assertEqual(scale(90, "thousand"), 90_000)
        self.assertEqual(scale(2_880_000, "unit"), 2_880_000)

    def test_missing_magnitude_is_a_plain_figure(self):
        self.assertEqual(scale(50_000, None), 50_000)


class TestAbsoluteValues(unittest.TestCase):
    def test_monthly_answer_to_a_yearly_field_is_annualised(self):
        # "2.4 lakh a month" against annual_income. The model reports the parts;
        # the x12 happens here.
        value = to_stored_value(
            INCOME, amount=2.4, magnitude="lakh", period="per_month", text_value=None
        )
        self.assertEqual(value, 2_880_000)

    def test_yearly_answer_to_a_monthly_field_is_divided(self):
        value = to_stored_value(
            EXPENSE, amount=12, magnitude="lakh", period="per_year", text_value=None
        )
        self.assertEqual(value, 100_000)

    def test_period_matching_the_column_is_left_alone(self):
        value = to_stored_value(
            INCOME, amount=32, magnitude="lakh", period="per_year", text_value=None
        )
        self.assertEqual(value, 3_200_000)

    def test_a_period_less_field_ignores_the_period(self):
        value = to_stored_value(
            ASSETS, amount=12, magnitude="lakh", period="none", text_value=None
        )
        self.assertEqual(value, 1_200_000)

    def test_no_period_on_a_periodic_field_is_a_question_not_a_guess(self):
        with self.assertRaises(AmbiguousUnit):
            to_stored_value(
                INCOME, amount=2.4, magnitude=None, period="none", text_value=None
            )

    def test_integer_fields_round(self):
        value = to_stored_value(
            RETIREMENT_AGE, amount=55.4, magnitude="unit", period=None, text_value=None
        )
        self.assertEqual(value, 55)
        self.assertIsInstance(value, int)

    def test_enum_takes_the_text(self):
        horizon = spec("investment_horizon")
        self.assertEqual(
            to_stored_value(
                horizon, amount=None, magnitude=None, period=None, text_value="5+ years"
            ),
            "5+ years",
        )


class TestRelativeChanges(unittest.TestCase):
    """"My income increased by 20%" — the instruction, resolved here."""

    def test_percentage_increase_uses_the_stored_value(self):
        value, basis = apply_relative(
            INCOME, 3_000_000, RelativeChange(direction="increase", pct=20)
        )
        self.assertEqual(value, 3_600_000)
        self.assertIn("20%", basis)
        self.assertIn("increase", basis)

    def test_percentage_decrease(self):
        value, _ = apply_relative(
            INCOME, 3_000_000, RelativeChange(direction="decrease", pct=10)
        )
        self.assertEqual(value, 2_700_000)

    def test_halving(self):
        sip = spec("starting_monthly_investment")
        value, _ = apply_relative(
            sip, 50_000, RelativeChange(direction="decrease", pct=50)
        )
        self.assertEqual(value, 25_000)

    def test_absolute_delta_in_the_columns_own_period(self):
        # "we spend 10k more a month" against a per-month column.
        value, basis = apply_relative(
            EXPENSE,
            90_000,
            RelativeChange(
                direction="increase", amount=10, magnitude="thousand", period="per_month"
            ),
        )
        self.assertEqual(value, 100_000)
        self.assertIn("more", basis)

    def test_absolute_delta_stated_monthly_against_a_yearly_column(self):
        # "I'm earning 20k more a month" against annual_income -> +2.4L a year.
        value, _ = apply_relative(
            INCOME,
            3_000_000,
            RelativeChange(
                direction="increase", amount=20, magnitude="thousand", period="per_month"
            ),
        )
        self.assertEqual(value, 3_240_000)

    def test_a_relative_change_with_nothing_on_file_is_refused(self):
        # A percentage of an unknown figure is not a figure. The caller turns
        # this into "what is it now?" rather than inventing a starting point.
        with self.assertRaises(NoBaseline):
            apply_relative(INCOME, None, RelativeChange(direction="increase", pct=20))

    def test_a_relative_change_on_a_non_numeric_field_is_refused(self):
        horizon = spec("investment_horizon")
        with self.assertRaises(NoBaseline):
            apply_relative(
                horizon, "5+ years", RelativeChange(direction="increase", pct=20)
            )

    def test_a_delta_with_no_period_on_a_periodic_field_is_refused(self):
        with self.assertRaises(AmbiguousUnit):
            apply_relative(
                INCOME,
                3_000_000,
                RelativeChange(
                    direction="increase", amount=2, magnitude="lakh", period="none"
                ),
            )

    def test_the_result_is_clamped_to_the_fields_rails(self):
        # A 200% rise on a 40% slab would put the tax rate past anything real;
        # the registry's own max is the ceiling.
        value, _ = apply_relative(
            SLAB, 40, RelativeChange(direction="increase", pct=200)
        )
        self.assertEqual(value, SLAB.max_value)

    def test_a_decrease_never_goes_negative(self):
        value, _ = apply_relative(
            ASSETS,
            100_000,
            RelativeChange(
                direction="decrease", amount=5, magnitude="lakh", period=None
            ),
        )
        self.assertEqual(value, 0.0)


if __name__ == "__main__":
    unittest.main()
