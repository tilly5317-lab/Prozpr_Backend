"""Misclassified fund-level redirect → risk counterfactual/clarify."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.services.ai_bridge.asset_allocation.chat import (
    ChatAction,
    _coerce_misclassified_redirect_action,
)


class CoerceRedirectActionTests(unittest.TestCase):
    def _last_alloc(self, risk: float = 7.0) -> MagicMock:
        return MagicMock(
            output_payload={
                "allocation_result": {
                    "client_summary": {"effective_risk_score": risk},
                },
            },
        )

    def test_more_risk_runs_counterfactual(self):
        action = ChatAction(
            mode="redirect",
            redirect_reason="fund-level question — please ask explicitly to rebalance",
        )
        out = _coerce_misclassified_redirect_action(
            "do asset allocation which more risk?",
            action,
            self._last_alloc(7.0),
        )
        self.assertEqual(out.mode, "counterfactual_explore")
        self.assertEqual(out.overrides, {"effective_risk_score": 8.5})

    def test_real_fund_question_stays_redirect(self):
        action = ChatAction(
            mode="redirect",
            redirect_reason="fund-level question",
        )
        out = _coerce_misclassified_redirect_action(
            "which large-cap fund should I pick?",
            action,
            self._last_alloc(),
        )
        self.assertEqual(out.mode, "redirect")


if __name__ == "__main__":
    unittest.main()
