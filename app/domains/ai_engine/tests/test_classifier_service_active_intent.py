"""classify_user_message forwards active_intent to ClassificationInput."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from app.domains.intent_classifier.services.intent_classifier_engine import (
    classify_user_message,
)


class ActiveIntentForwardingTests(unittest.TestCase):
    def test_active_intent_forwarded_to_classification_input(self):
        captured = {}

        async def fake_aclassify(inp):
            captured["active_intent"] = inp.active_intent
            return MagicMock(
                intent=MagicMock(value="asset_allocation"),
                confidence=0.9,
                reasoning="...",
                out_of_scope_message=None,
            )

        with patch(
            "app.domains.intent_classifier.services.intent_classifier_engine._get_classifier"
        ) as gc:
            classifier = MagicMock()
            classifier.aclassify = fake_aclassify
            gc.return_value = classifier

            asyncio.run(
                classify_user_message(
                    customer_question="is this too aggressive?",
                    conversation_history=[],
                    active_intent="asset_allocation",
                )
            )

        self.assertIsNotNone(captured["active_intent"])
        self.assertEqual(captured["active_intent"].value, "asset_allocation")


if __name__ == "__main__":
    unittest.main()
