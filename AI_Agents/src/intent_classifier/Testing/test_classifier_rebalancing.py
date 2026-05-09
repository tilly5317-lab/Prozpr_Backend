"""Locks the REBALANCING intent boundary."""

from __future__ import annotations

import os

import pytest

from intent_classifier.classifier import IntentClassifier
from intent_classifier.models import ClassificationInput, Intent


_skip_no_api_key = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set; skipping live classifier call",
)


@_skip_no_api_key
@pytest.mark.parametrize("question", [
    "rebalance my portfolio",
    "what trades should I make to align with my plan?",
    "show me what to buy and sell",
])
def test_rebalancing_intent_classified(question: str) -> None:
    classifier = IntentClassifier()
    result = classifier.classify(ClassificationInput(customer_question=question))
    assert result.intent == Intent.REBALANCING, (
        f"expected REBALANCING for {question!r}, got {result.intent}"
    )


@_skip_no_api_key
def test_optimisation_still_routes_to_optimisation() -> None:
    """Guards against regression: 'where should I be' should NOT be rebalancing."""
    classifier = IntentClassifier()
    result = classifier.classify(
        ClassificationInput(customer_question="what's my ideal asset allocation?")
    )
    assert result.intent == Intent.ASSET_ALLOCATION
