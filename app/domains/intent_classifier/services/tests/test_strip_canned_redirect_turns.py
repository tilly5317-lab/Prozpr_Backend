"""Unit tests for the classifier's canned-redirect history scrub (audit F7).

Two-tier detection: intent tag on new rows, frozen legacy prefixes on old
(untagged) rows.
"""

from __future__ import annotations

import unittest

import app.all_models  # noqa: F401  (register ORM relationships)

# Import the ai_engine package FIRST: entering this cluster via
# intent_classifier_engine directly trips a pre-existing circular import
# (engine → ai_engine.common → ai_engine/__init__ → brain → engine). The
# production process always initializes ai_engine before the engine module.
import app.domains.ai_engine  # noqa: F401
from app.domains.intent_classifier.services.intent_classifier_engine import (
    _strip_canned_redirect_turns,
)

from intent_classifier.prompts import OUT_OF_SCOPE_MESSAGE  # noqa: E402


def _user(content: str, intent: str | None = None) -> dict:
    return {"role": "user", "content": content, "intent": intent}


def _assistant(content: str, intent: str | None = None) -> dict:
    return {"role": "assistant", "content": content, "intent": intent}


class TaggedRowTests(unittest.TestCase):
    def test_tagged_redirect_turn_dropped_with_its_user_message(self):
        history = [
            _user("what's my equity split?"),
            _assistant("You hold 60% equity.", intent="portfolio_query"),
            _user("tell me a joke"),
            _assistant("I'm PI — happy to help with your portfolio.", intent="out_of_scope"),
            _user("and my debt split?"),
        ]

        out = _strip_canned_redirect_turns(history)

        self.assertEqual(
            [m["content"] for m in out],
            ["what's my equity split?", "You hold 60% equity.", "and my debt split?"],
        )

    def test_tailored_redirect_is_dropped_by_tag_despite_novel_text(self):
        """LLM-tailored redirects share no prefix with canned text — the tag
        is the only thing that catches them (the pre-F7 scrub missed these)."""
        history = [
            _user("should I buy Infosys?"),
            _assistant(
                "Great question about Infosys! That said, individual stock calls "
                "aren't something I advise on…",
                intent="stock_advice",
            ),
            _user("ok then how is my portfolio doing?"),
        ]

        out = _strip_canned_redirect_turns(history)

        self.assertEqual([m["content"] for m in out], ["ok then how is my portfolio doing?"])

    def test_tagged_specialist_turn_survives_even_if_text_resembles_canned(self):
        """The tag wins over text: a genuine reply is kept regardless of wording."""
        history = [
            _user("what can you do?"),
            _assistant(OUT_OF_SCOPE_MESSAGE, intent="general_chat"),
        ]

        out = _strip_canned_redirect_turns(history)

        self.assertEqual(len(out), 2)


class UntaggedLegacyRowTests(unittest.TestCase):
    def test_untagged_canned_prefix_still_dropped(self):
        """Rows written before the intent column rely on prefix matching."""
        history = [
            _user("tell me a joke"),
            _assistant(OUT_OF_SCOPE_MESSAGE),  # intent=None (legacy row)
            _user("what do I hold?"),
            _assistant("You hold 12 funds."),
        ]

        out = _strip_canned_redirect_turns(history)

        self.assertEqual(
            [m["content"] for m in out],
            ["what do I hold?", "You hold 12 funds."],
        )

    def test_untagged_normal_reply_kept(self):
        history = [
            _user("what do I hold?"),
            _assistant("You hold 12 funds."),
        ]

        self.assertEqual(_strip_canned_redirect_turns(history), history)


if __name__ == "__main__":
    unittest.main()
