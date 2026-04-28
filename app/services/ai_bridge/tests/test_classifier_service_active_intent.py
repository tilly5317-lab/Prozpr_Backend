"""classify_user_message forwards active_intent to ClassificationInput."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from app.services.ai_bridge.intent_classifier_service import classify_user_message


async def _wrap_sync(fn, *args, **kwargs):
    return fn(*args, **kwargs)


class ActiveIntentForwardingTests(unittest.TestCase):

    def test_active_intent_forwarded_to_classification_input(self):
        captured = {}

        def fake_classify(self, inp):
            captured["active_intent"] = inp.active_intent
            return MagicMock(intent=MagicMock(value="portfolio_optimisation"),
                              confidence=0.9, is_follow_up=True,
                              reasoning="...",
                              out_of_scope_message=None)

        with patch("app.services.ai_bridge.intent_classifier_service._get_classifier") as gc, \
             patch.object(asyncio, "to_thread", new=_wrap_sync):
            classifier = MagicMock()
            classifier.classify = lambda inp: fake_classify(classifier, inp)
            gc.return_value = classifier

            asyncio.run(classify_user_message(
                customer_question="is this too aggressive?",
                conversation_history=[],
                active_intent="portfolio_optimisation",
            ))

        self.assertIsNotNone(captured["active_intent"])
        self.assertEqual(captured["active_intent"].value, "portfolio_optimisation")


if __name__ == "__main__":
    unittest.main()
