"""The redaction boundary.

Two failure modes, and they pull in opposite directions: leaking an identifier
into a prompt, and eating a rupee figure that the extractor then reads as a
different number. The second is the one that silently corrupts a projection, so
the money cases here are as load-bearing as the identifier ones.
"""

from __future__ import annotations

import unittest

from app.domains.financial_planning.services import privacy


class TestIdentifiersAreStripped(unittest.TestCase):
    def test_pan(self):
        out = privacy.redact("my PAN is ABCDE1234F, what's my tax?")
        self.assertNotIn("ABCDE1234F", out)
        self.assertIn("[pan]", out)

    def test_email_and_phone(self):
        out = privacy.redact("mail me at raj.k@example.com or call 9876543210")
        self.assertNotIn("raj.k@example.com", out)
        self.assertNotIn("9876543210", out)

    def test_aadhaar_grouped_or_not(self):
        self.assertIn("[aadhaar]", privacy.redact("aadhaar 1234 5678 9012"))
        self.assertIn("[aadhaar]", privacy.redact("aadhaar 123456789012"))

    def test_long_account_number(self):
        out = privacy.redact("credit it to 50100234567890 please")
        self.assertNotIn("50100234567890", out)


class TestMoneySurvives(unittest.TestCase):
    """A redactor that eats amounts is worse than no redactor at all."""

    def test_indian_grouped_amount(self):
        text = "my income is Rs 28,80,000 a year"
        self.assertEqual(privacy.redact(text), text)

    def test_bare_amount(self):
        text = "I have 2880000 in FDs"
        self.assertEqual(privacy.redact(text), text)

    def test_magnitude_words(self):
        text = "2.4 lakh a month, and about 1.5 crore saved"
        self.assertEqual(privacy.redact(text), text)

    def test_a_percentage_change(self):
        text = "my salary went up 20% this year"
        self.assertEqual(privacy.redact(text), text)

    def test_a_year_is_not_an_identifier(self):
        text = "I want the house by 2032"
        self.assertEqual(privacy.redact(text), text)


class TestHistory(unittest.TestCase):
    def test_capped_scrubbed_and_truncated(self):
        history = [
            {"role": "user", "content": f"message {i} from 9876543210"}
            for i in range(10)
        ]
        out = privacy.redact_history(history, max_turns=3, max_chars=20)
        self.assertEqual(len(out), 3)
        for msg in out:
            self.assertNotIn("9876543210", msg["content"])
            self.assertLessEqual(len(msg["content"]), 20)

    def test_empty_history(self):
        self.assertEqual(privacy.redact_history(None, max_turns=4), [])


class TestAuditVerbatim(unittest.TestCase):
    def test_scrubbed_and_capped(self):
        out = privacy.verbatim_for_audit("PAN ABCDE1234F, income 32 lakh", limit=100)
        self.assertNotIn("ABCDE1234F", out)
        self.assertIn("32 lakh", out)

    def test_empty_becomes_none(self):
        self.assertIsNone(privacy.verbatim_for_audit("   "))


if __name__ == "__main__":
    unittest.main()
