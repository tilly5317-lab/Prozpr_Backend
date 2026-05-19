"""Offline unit tests for ``intent_classifier`` (CI: ``unittest AI_Agents.tests.test_intent_classifier``).

No Anthropic API calls — validates schema drift guards and deterministic helpers only.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import get_args

# CI sets PYTHONPATH to AI_Agents/src; unittest loads this file via AI_Agents.tests.*
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from intent_classifier.classifier import (  # noqa: E402
    _IntentLiteral,
    _OutOfScopeSubreasonLiteral,
    _build_user_turn,
    _format_history,
)
from intent_classifier.models import (  # noqa: E402
    ClassificationInput,
    ConversationMessage,
    Intent,
    OutOfScopeSubreason,
)


class TestIntentSchemaDrift(unittest.TestCase):
    """Keep LLM tool-schema literals aligned with Pydantic enums."""

    def test_intent_literal_matches_enum(self) -> None:
        self.assertEqual(
            set(get_args(_IntentLiteral)),
            {member.value for member in Intent},
        )

    def test_out_of_scope_subreason_literal_matches_enum(self) -> None:
        self.assertEqual(
            set(get_args(_OutOfScopeSubreasonLiteral)),
            {member.value for member in OutOfScopeSubreason},
        )


class TestIntentClassifierHelpers(unittest.TestCase):
    def test_format_history_empty(self) -> None:
        self.assertEqual(_format_history([]), "")

    def test_format_history_truncates_to_last_twelve(self) -> None:
        history = [
            ConversationMessage(role="user", content=f"msg-{i}")
            for i in range(20)
        ]
        block = _format_history(history)
        self.assertIn("msg-19", block)
        self.assertNotIn("msg-0\n", block)
        self.assertIn("Customer: msg-19", block)

    def test_build_user_turn_includes_question_and_active_intent(self) -> None:
        inp = ClassificationInput(
            customer_question="What is my equity allocation?",
            conversation_history=[],
            active_intent=Intent.ASSET_ALLOCATION,
        )
        turn = _build_user_turn(inp)
        self.assertIn("What is my equity allocation?", turn)
        self.assertIn("asset_allocation", turn)
        self.assertIn("classify_intent", turn.lower())


if __name__ == "__main__":
    unittest.main()
