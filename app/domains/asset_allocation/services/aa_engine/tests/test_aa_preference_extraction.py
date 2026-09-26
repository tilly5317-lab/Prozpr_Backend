"""AA reads an asset-class/category ask as a PREFERENCE; bare risk-appetite
words still move the risk score (spec 2026-09-16 D5 — the asymmetry is
deliberate: this domain owns the risk profile, rebalancing has no risk lever).

The coercion only runs on a redirect whose reason names a fund-level question;
with any other reason it returns the action untouched, so a test that omits
`_FUND_LEVEL` passes trivially and proves nothing.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.domains.asset_allocation.services.aa_engine.chat import (
    ChatAction,
    _coerce_misclassified_redirect_action,
)

_FUND_LEVEL = "fund-level question — please ask explicitly to rebalance"


class PreferenceAsksOnTheActionTests(unittest.TestCase):
    def test_the_action_schema_carries_preference_asks(self):
        action = ChatAction(
            mode="counterfactual_explore",
            preference_asks=[{"target": "gold", "level": "more"}],
        )

        self.assertEqual(action.preference_asks[0].target, "gold")
        self.assertEqual(action.preference_asks[0].level, "more")

    def test_a_stated_percentage_rides_along(self):
        action = ChatAction(
            mode="counterfactual_explore",
            preference_asks=[{"target": "equity", "level": "number", "number": 70}],
        )

        self.assertEqual(action.preference_asks[0].number, 70)


class CoercionLeavesPreferenceWordsAloneTests(unittest.TestCase):
    def _last_alloc(self, risk: float = 5.5) -> MagicMock:
        return MagicMock(
            output_payload={
                "allocation_result": {"client_summary": {"effective_risk_score": risk}}
            }
        )

    def test_class_words_are_not_turned_into_a_risk_score(self):
        """Today this returns a clarify offering to LOWER risk to 4.0 — the
        customer asked for more equity."""
        action = ChatAction(mode="redirect", redirect_reason=_FUND_LEVEL)

        out = _coerce_misclassified_redirect_action(
            "push my equity up a bit", action, self._last_alloc()
        )

        self.assertIs(out, action, "a class ask belongs to preference_asks")

    def test_a_percentage_on_a_class_is_not_a_risk_score(self):
        """Today the digit is read as a risk score: 70 on a 1-10 scale."""
        action = ChatAction(mode="redirect", redirect_reason=_FUND_LEVEL)

        out = _coerce_misclassified_redirect_action(
            "take my equity to 70%", action, self._last_alloc()
        )

        self.assertIs(out, action)

    def test_category_words_are_not_turned_into_a_risk_score(self):
        action = ChatAction(mode="redirect", redirect_reason=_FUND_LEVEL)

        out = _coerce_misclassified_redirect_action(
            "I don't want any small cap funds", action, self._last_alloc()
        )

        self.assertIs(out, action)

    def test_bare_risk_words_still_reach_the_risk_score(self):
        """D5: with no asset class named, this domain's lever is the score."""
        action = ChatAction(mode="redirect", redirect_reason=_FUND_LEVEL)

        out = _coerce_misclassified_redirect_action(
            "do asset allocation which more risk?", action, self._last_alloc()
        )

        self.assertEqual(out.mode, "counterfactual_explore")
        self.assertIn("effective_risk_score", out.overrides or {})
