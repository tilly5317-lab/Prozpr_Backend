"""
Unit tests for IntentClassifier.

All tests mock at the chain.invoke level — no live API calls are made.
"""

import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Minimal stubs so the module can be imported without installed packages
# ---------------------------------------------------------------------------

def _stub_dotenv():
    if "dotenv" not in sys.modules:
        pkg = types.ModuleType("dotenv")
        pkg.load_dotenv = lambda: None
        sys.modules["dotenv"] = pkg


def _stub_langchain():
    """Stub LangChain packages if not installed."""
    if "langchain_anthropic" not in sys.modules:
        lc_anthropic = types.ModuleType("langchain_anthropic")
        lc_anthropic.ChatAnthropic = MagicMock
        sys.modules["langchain_anthropic"] = lc_anthropic

    if "langchain_core" not in sys.modules:
        sys.modules["langchain_core"] = types.ModuleType("langchain_core")

    if "langchain_core.messages" not in sys.modules:
        messages_mod = types.ModuleType("langchain_core.messages")

        class _FakeMessage:
            def __init__(self, content):
                self.content = content

        messages_mod.SystemMessage = _FakeMessage
        messages_mod.HumanMessage = _FakeMessage
        sys.modules["langchain_core.messages"] = messages_mod
        sys.modules["langchain_core"].messages = messages_mod


_stub_dotenv()
_stub_langchain()

# ---------------------------------------------------------------------------
# Now import the module under test
# ---------------------------------------------------------------------------

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from intent_classifier import (
    ClassificationInput,
    ConversationMessage,
    Intent,
    IntentClassifier,
)
from intent_classifier.prompts import GOAL_PLANNING_MESSAGE, OUT_OF_SCOPE_MESSAGE, STOCK_ADVICE_MESSAGE


# ---------------------------------------------------------------------------
# Helper: build a fake LLM output for a given intent
# ---------------------------------------------------------------------------

def _make_mock_llm_output(intent: str, confidence: float = 0.95, reasoning: str = "test",
                          is_follow_up: bool = False):
    """Return an object matching _LLMOutput's attribute interface."""
    out = MagicMock()
    out.intent = intent
    out.confidence = confidence
    out.is_follow_up = is_follow_up
    out.reasoning = reasoning
    out.wants_fresh_recomputation = False
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestIntentClassifier(unittest.TestCase):

    def _classifier_with_mock(self, mock_output):
        """Return an IntentClassifier whose chain.invoke returns mock_output."""
        with patch("intent_classifier.classifier.ChatAnthropic"):
            classifier = IntentClassifier(api_key="test-key")
        classifier.chain = MagicMock()
        classifier.chain.invoke = MagicMock(return_value=mock_output)
        return classifier

    # --- Portfolio Optimisation ---

    def test_portfolio_optimisation_asset_allocation(self):
        mock = _make_mock_llm_output("portfolio_optimisation", 0.93, "Customer asking about asset allocation.")
        classifier = self._classifier_with_mock(mock)

        result = classifier.classify(ClassificationInput(
            customer_question="Am I too heavy in equities right now?"
        ))

        self.assertEqual(result.intent, Intent.PORTFOLIO_OPTIMISATION)
        self.assertAlmostEqual(result.confidence, 0.93)
        self.assertIsNone(result.out_of_scope_message)

    def test_portfolio_optimisation_fund_switch(self):
        mock = _make_mock_llm_output("portfolio_optimisation", 0.97, "Fund switch is a portfolio action.")
        classifier = self._classifier_with_mock(mock)

        result = classifier.classify(ClassificationInput(
            customer_question="Should I switch from Axis Bluechip to Mirae Asset Large Cap?"
        ))

        self.assertEqual(result.intent, Intent.PORTFOLIO_OPTIMISATION)
        self.assertAlmostEqual(result.confidence, 0.97)
        self.assertIsNone(result.out_of_scope_message)

    # --- Portfolio Query ---

    def test_portfolio_query(self):
        mock = _make_mock_llm_output("portfolio_query", 0.94, "Customer asking for a count of their holdings.")
        classifier = self._classifier_with_mock(mock)

        result = classifier.classify(ClassificationInput(
            customer_question="How many mutual funds do I currently hold?"
        ))

        self.assertEqual(result.intent, Intent.PORTFOLIO_QUERY)
        self.assertAlmostEqual(result.confidence, 0.94)
        self.assertIsNone(result.out_of_scope_message)

    # --- General Market Query ---

    def test_general_market_query(self):
        mock = _make_mock_llm_output("general_market_query", 0.90, "General market performance question.")
        classifier = self._classifier_with_mock(mock)

        result = classifier.classify(ClassificationInput(
            customer_question="How are mid-cap funds performing this year?"
        ))

        self.assertEqual(result.intent, Intent.GENERAL_MARKET_QUERY)
        self.assertAlmostEqual(result.confidence, 0.90)
        self.assertIsNone(result.out_of_scope_message)

    def test_investment_timing_question_is_portfolio_optimisation(self):
        # "Is this a good time to invest in X?" implies advice → portfolio_optimisation, NOT general_market_query
        mock = _make_mock_llm_output("portfolio_optimisation", 0.92, "Investment timing question implies a recommendation.")
        classifier = self._classifier_with_mock(mock)

        result = classifier.classify(ClassificationInput(
            customer_question="Is this a good time to invest in gold?"
        ))

        self.assertEqual(result.intent, Intent.PORTFOLIO_OPTIMISATION)
        self.assertAlmostEqual(result.confidence, 0.92)
        self.assertIsNone(result.out_of_scope_message)

    # --- Goal Planning (coming soon — returns holding message) ---

    def test_goal_planning_sets_message(self):
        mock = _make_mock_llm_output("goal_planning", 0.91, "Customer has a retirement goal.")
        classifier = self._classifier_with_mock(mock)

        result = classifier.classify(ClassificationInput(
            customer_question="I want to retire in 15 years with 5 crore — is that possible?"
        ))

        self.assertEqual(result.intent, Intent.GOAL_PLANNING)
        self.assertAlmostEqual(result.confidence, 0.91)
        self.assertEqual(result.out_of_scope_message, GOAL_PLANNING_MESSAGE)

    # --- Stock Advice (redirects to mutual funds) ---

    def test_stock_advice_sets_message(self):
        mock = _make_mock_llm_output("stock_advice", 0.96, "Direct stock buy question.")
        classifier = self._classifier_with_mock(mock)

        result = classifier.classify(ClassificationInput(
            customer_question="Should I buy Infosys shares right now?"
        ))

        self.assertEqual(result.intent, Intent.STOCK_ADVICE)
        self.assertEqual(result.out_of_scope_message, STOCK_ADVICE_MESSAGE)

    # --- Out of Scope ---

    def test_out_of_scope_sets_message(self):
        mock = _make_mock_llm_output("out_of_scope", 0.80, "Question is about crypto — not supported.")
        classifier = self._classifier_with_mock(mock)

        result = classifier.classify(ClassificationInput(
            customer_question="Should I buy Bitcoin right now?"
        ))

        self.assertEqual(result.intent, Intent.OUT_OF_SCOPE)
        self.assertEqual(result.out_of_scope_message, OUT_OF_SCOPE_MESSAGE)

    # --- Conversation history is forwarded ---

    def test_conversation_history_included_in_api_call(self):
        mock = _make_mock_llm_output("portfolio_optimisation", 0.88, "Follow-up on prior discussion.")
        classifier = self._classifier_with_mock(mock)

        history = [
            ConversationMessage(role="user", content="What is my current equity allocation?"),
            ConversationMessage(role="assistant", content="Your equity allocation is 70%."),
        ]
        classifier.classify(ClassificationInput(
            customer_question="What about gold?",
            conversation_history=history,
        ))

        call_args = classifier.chain.invoke.call_args
        messages = call_args[0][0]  # first positional arg = messages list
        # HumanMessage is the second element; its content is the user turn string
        user_content: str = messages[1].content

        self.assertIn("What is my current equity allocation?", user_content)
        self.assertIn("Your equity allocation is 70%.", user_content)
        self.assertIn("What about gold?", user_content)

    # --- History truncation: only last 6 messages sent ---

    def test_history_truncated_to_last_6_messages(self):
        mock = _make_mock_llm_output("portfolio_query", 0.85, "Portfolio question.")
        classifier = self._classifier_with_mock(mock)

        # 10 messages — only last 6 should be sent
        history = [
            ConversationMessage(role="user", content=f"message {i}")
            for i in range(10)
        ]
        classifier.classify(ClassificationInput(
            customer_question="Will I meet my goal?",
            conversation_history=history,
        ))

        call_args = classifier.chain.invoke.call_args
        messages = call_args[0][0]
        user_content: str = messages[1].content

        # message 0–3 should be dropped
        self.assertNotIn("message 0", user_content)
        self.assertNotIn("message 3", user_content)
        # message 4–9 should be present
        self.assertIn("message 4", user_content)
        self.assertIn("message 9", user_content)

    # --- Follow-up detection ---

    def test_follow_up_flag_true(self):
        mock = _make_mock_llm_output("portfolio_optimisation", 0.91, "Continuing allocation discussion.", is_follow_up=True)
        classifier = self._classifier_with_mock(mock)

        history = [
            ConversationMessage(role="user", content="Should I rebalance my portfolio?"),
            ConversationMessage(role="assistant", content="Your equity allocation is high. Consider shifting to debt."),
        ]
        result = classifier.classify(ClassificationInput(
            customer_question="Yes, go ahead with that.",
            conversation_history=history,
            active_intent=Intent.PORTFOLIO_OPTIMISATION,
        ))

        self.assertEqual(result.intent, Intent.PORTFOLIO_OPTIMISATION)
        self.assertTrue(result.is_follow_up)

    def test_follow_up_flag_false_for_new_topic(self):
        mock = _make_mock_llm_output("portfolio_query", 0.93, "New informational question.", is_follow_up=False)
        classifier = self._classifier_with_mock(mock)

        result = classifier.classify(ClassificationInput(
            customer_question="How many mutual funds do I own?",
        ))

        self.assertEqual(result.intent, Intent.PORTFOLIO_QUERY)
        self.assertFalse(result.is_follow_up)

    def test_follow_up_default_false_when_no_history(self):
        mock = _make_mock_llm_output("general_market_query", 0.88, "Market question with no prior context.")
        classifier = self._classifier_with_mock(mock)

        result = classifier.classify(ClassificationInput(
            customer_question="How are mid-cap funds doing?"
        ))

        self.assertFalse(result.is_follow_up)

    def test_active_intent_included_in_user_turn(self):
        mock = _make_mock_llm_output("portfolio_optimisation", 0.90, "Follow-up.", is_follow_up=True)
        classifier = self._classifier_with_mock(mock)

        classifier.classify(ClassificationInput(
            customer_question="What about gold?",
            active_intent=Intent.PORTFOLIO_OPTIMISATION,
        ))

        call_args = classifier.chain.invoke.call_args
        messages = call_args[0][0]
        user_content: str = messages[1].content

        self.assertIn("Currently active intent: portfolio_optimisation", user_content)

    def test_active_intent_omitted_when_none(self):
        mock = _make_mock_llm_output("portfolio_query", 0.92, "Fresh question.")
        classifier = self._classifier_with_mock(mock)

        classifier.classify(ClassificationInput(
            customer_question="Show me my holdings.",
        ))

        call_args = classifier.chain.invoke.call_args
        messages = call_args[0][0]
        user_content: str = messages[1].content

        self.assertNotIn("Currently active intent", user_content)

    # --- Chain errors propagate ---

    def test_chain_error_propagates(self):
        with patch("intent_classifier.classifier.ChatAnthropic"):
            classifier = IntentClassifier(api_key="test-key")
        classifier.chain = MagicMock()
        classifier.chain.invoke = MagicMock(side_effect=RuntimeError("LLM failed"))

        with self.assertRaises(RuntimeError):
            classifier.classify(ClassificationInput(customer_question="Hello?"))

    # --- Confidence bounds preserved ---

    def test_confidence_stored_correctly(self):
        mock = _make_mock_llm_output("portfolio_query", 0.5)
        classifier = self._classifier_with_mock(mock)
        result = classifier.classify(ClassificationInput(customer_question="Any question"))
        self.assertAlmostEqual(result.confidence, 0.5)

    # --- Prompt caching: system message uses cache_control ---

    def test_system_message_has_cache_control(self):
        mock = _make_mock_llm_output("portfolio_query", 0.9)
        classifier = self._classifier_with_mock(mock)
        classifier.classify(ClassificationInput(customer_question="Show me my holdings."))

        call_args = classifier.chain.invoke.call_args
        messages = call_args[0][0]
        system_content = messages[0].content  # SystemMessage content is a list

        self.assertIsInstance(system_content, list)
        self.assertEqual(system_content[0]["cache_control"], {"type": "ephemeral"})


class _FakeLLMOut:
    """Stand-in for the LangChain-structured LLM output."""
    def __init__(self, *, intent, confidence, is_follow_up, reasoning,
                 wants_fresh_recomputation=False):
        self.intent = intent
        self.confidence = confidence
        self.is_follow_up = is_follow_up
        self.reasoning = reasoning
        self.wants_fresh_recomputation = wants_fresh_recomputation


class WantsFreshRecomputationFieldTests(unittest.TestCase):
    """The classifier returns a wants_fresh_recomputation flag."""

    def test_default_false_for_explanation_question(self):
        from intent_classifier import IntentClassifier, ClassificationInput
        clf = IntentClassifier(api_key="sk-fake")
        clf.chain = MagicMock()
        clf.chain.invoke.return_value = _FakeLLMOut(
            intent="portfolio_optimisation", confidence=0.9,
            is_follow_up=True, reasoning="explanation",
            wants_fresh_recomputation=False,
        )

        result = clf.classify(ClassificationInput(
            customer_question="is this too aggressive?",
        ))
        self.assertFalse(result.wants_fresh_recomputation)

    def test_true_when_user_asks_for_redo(self):
        from intent_classifier import IntentClassifier, ClassificationInput
        clf = IntentClassifier(api_key="sk-fake")
        clf.chain = MagicMock()
        clf.chain.invoke.return_value = _FakeLLMOut(
            intent="portfolio_optimisation", confidence=0.9,
            is_follow_up=True, reasoning="redo with new money",
            wants_fresh_recomputation=True,
        )

        result = clf.classify(ClassificationInput(
            customer_question="actually I have 10L more, redo this",
        ))
        self.assertTrue(result.wants_fresh_recomputation)


if __name__ == "__main__":
    unittest.main()
