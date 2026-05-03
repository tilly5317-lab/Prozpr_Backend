"""Drift guard: classifier Literal must stay in sync with the ``Intent`` enum.

If a new intent is added to ``Intent`` but the ``_IntentLiteral`` in
``classifier._LLMOutput`` is not updated, the LLM tool schema silently keeps
the old enum and the new intent becomes un-emittable. This test catches that
drift before it ships.
"""

from __future__ import annotations

from typing import get_args

from app.services.ai_bridge.common import ensure_ai_agents_path

ensure_ai_agents_path()

from intent_classifier.classifier import _IntentLiteral  # type: ignore[import-not-found]
from intent_classifier.models import Intent  # type: ignore[import-not-found]


def test_intent_literal_matches_enum() -> None:
    """``_IntentLiteral`` in classifier.py must list every Intent enum value."""
    assert set(get_args(_IntentLiteral)) == {i.value for i in Intent}
