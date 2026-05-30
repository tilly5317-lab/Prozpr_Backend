"""TEMP(rebalancing-chat): keyword override tests — remove with _temp_chat_deferral.py."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.domains.ai_engine.services.bridges.intent_classifier_service import (
    _apply_rebalancing_keyword_override,
)


class RebalancingKeywordOverrideTests(unittest.TestCase):
    def _result(self, intent_value: str):
        from intent_classifier.models import Intent

        return MagicMock(
            intent=Intent(intent_value),
            confidence=0.8,
            reasoning="llm said so",
            out_of_scope_message=None,
        )

    def test_rebalance_my_portfolio_from_asset_allocation(self):
        from intent_classifier.models import Intent

        raw = self._result("asset_allocation")
        out = _apply_rebalancing_keyword_override(
            "please rebalance my portfolio", raw,
        )
        self.assertEqual(out.intent, Intent.REBALANCING)
        self.assertGreaterEqual(out.confidence, 0.9)

    def test_typo_relablace(self):
        from intent_classifier.models import Intent

        raw = self._result("asset_allocation")
        out = _apply_rebalancing_keyword_override("relablace my portfolio", raw)
        self.assertEqual(out.intent, Intent.REBALANCING)

    def test_no_override_without_rebalance_word(self):
        from intent_classifier.models import Intent

        raw = self._result("asset_allocation")
        out = _apply_rebalancing_keyword_override(
            "what should my ideal allocation be?", raw,
        )
        self.assertEqual(out.intent, Intent.ASSET_ALLOCATION)

    def test_already_rebalancing_unchanged(self):
        raw = self._result("rebalancing")
        out = _apply_rebalancing_keyword_override("rebalance", raw)
        self.assertIs(out, raw)


if __name__ == "__main__":
    unittest.main()
