"""The field registry and the write router that reads it.

The registry's whole reason to exist is that ONE file decides where a profile
field lives and what the allowed answers are. These tests guard the two ways
that promise breaks in practice:

  * a registry entry names a table or column the write router cannot reach, so
    chat happily "saves" into nowhere;
  * chat offers an option string /profile/complete never writes, so the same
    column ends up with two vocabularies (this actually happened during the
    build: the emergency-fund options were invented rather than copied).
"""

from __future__ import annotations

import unittest
from datetime import date

from app.domains.profile.models.investment_profile import InvestmentProfile
from app.domains.profile.models.personal_finance_profile import PersonalFinanceProfile
from app.domains.profile.models.risk_profile import RiskProfile
from app.domains.profile.models.tax_profile import TaxProfile
from app.domains.identity.models.user import User
from app.domains.profile.services.profile_field_registry import (
    FIELD_REGISTRY,
    NEVER_GATED_INTENTS,
    REQUIREMENTS,
    specs_for,
)
from app.domains.profile.services.profile_write_router import (
    FieldValidationError,
    validate_value,
)

_MODELS = {
    "users": User,
    "personal_finance_profiles": PersonalFinanceProfile,
    "investment_profiles": InvestmentProfile,
    "risk_profiles": RiskProfile,
    "tax_profiles": TaxProfile,
}


class TestRegistryIntegrity(unittest.TestCase):
    def test_every_field_points_at_a_real_column(self):
        for key, fs in FIELD_REGISTRY.items():
            with self.subTest(field=key):
                model = _MODELS.get(fs.table)
                self.assertIsNotNone(model, f"{key}: no writer for table {fs.table}")
                self.assertTrue(
                    hasattr(model, fs.column),
                    f"{key}: {fs.table}.{fs.column} is not a column on {model.__name__}",
                )

    def test_enum_fields_declare_options_and_others_do_not(self):
        for key, fs in FIELD_REGISTRY.items():
            with self.subTest(field=key):
                if fs.input_kind == "enum":
                    self.assertTrue(fs.options, f"{key}: enum with no options")
                else:
                    self.assertFalse(fs.options, f"{key}: options on a non-enum")

    def test_every_requirement_names_a_registry_field(self):
        for intent, req in REQUIREMENTS.items():
            for key in (*req.hard, *req.soft, *req.conditional_hard):
                with self.subTest(intent=intent, field=key):
                    self.assertIn(key, FIELD_REGISTRY)

    def test_read_only_intents_have_no_requirements(self):
        # A requirement row on a never-gated intent would be dead config that
        # reads as though those questions could block.
        for intent in NEVER_GATED_INTENTS:
            self.assertNotIn(intent, REQUIREMENTS, f"{intent} must never be gated")

    def test_specs_for_orders_by_priority_and_drops_unknowns(self):
        ordered = specs_for(["drop_reaction", "annual_income", "not_a_field"])
        self.assertEqual([f.key for f in ordered], ["annual_income", "drop_reaction"])


class TestValidation(unittest.TestCase):
    def test_indian_grouping_and_symbols_are_accepted(self):
        self.assertEqual(validate_value("annual_income", "28,80,000"), 2880000.0)
        self.assertEqual(validate_value("annual_income", "₹2880000"), 2880000.0)

    def test_zero_is_a_real_answer(self):
        # A customer with no loans answers 0; treating it as missing would
        # re-ask forever.
        self.assertEqual(
            validate_value("financial_liabilities_excl_mortgage", "0"), 0.0
        )

    def test_partial_enum_answers_resolve_to_the_stored_string(self):
        self.assertEqual(validate_value("tax_regime", "new"), "new")
        self.assertEqual(validate_value("investment_horizon", "5+"), "5+ years")

    def test_ambiguous_or_unknown_enum_answers_are_rejected(self):
        with self.assertRaises(FieldValidationError):
            validate_value("investment_horizon", "maybe")

    def test_indian_date_order_is_read_correctly(self):
        self.assertEqual(
            validate_value("date_of_birth", "12/03/1985"), date(1985, 3, 12)
        )
        self.assertEqual(
            validate_value("date_of_birth", "1985-03-12"), date(1985, 3, 12)
        )

    def test_out_of_range_values_are_rejected(self):
        for key, value in (
            ("income_tax_rate", "300"),
            ("retirement_age", "12"),
            ("annual_income", "-5"),
        ):
            with self.subTest(field=key):
                with self.assertRaises(FieldValidationError):
                    validate_value(key, value)

    def test_a_date_of_birth_implying_an_impossible_age_is_rejected(self):
        with self.assertRaises(FieldValidationError):
            validate_value("date_of_birth", f"{date.today().year}-01-01")

    def test_junk_and_unknown_fields_are_rejected(self):
        with self.assertRaises(FieldValidationError):
            validate_value("annual_income", "quite a lot")
        with self.assertRaises(FieldValidationError):
            validate_value("not_a_field", "1")


if __name__ == "__main__":
    unittest.main()
