"""Classification is Anthropic-only — there is no OpenAI fallback.

The fallback was unused (no OPENAI_API_KEY in the deployment) and silently
degraded behaviour when it did run: it never attached the stock-advice refusal
message, so `brain.py`'s classifier-only short-circuit was skipped and
"should I buy X stock?" fell through to general_chat and got answered.

A classifier failure must now surface as a failure, which the brain already
handles with its own recovery message.
"""

from __future__ import annotations

import pytest

# Must precede the engine import: importing it first hits a pre-existing cycle
# (engine -> ai_engine.common -> ai_engine package -> brain -> classifier service
# -> engine). Real startup and full-suite runs import ai_engine first.
import app.domains.ai_engine  # noqa: F401
import app.domains.intent_classifier.services.intent_classifier_engine as engine


@pytest.mark.asyncio
async def test_classifier_failure_propagates_unchanged(monkeypatch):
    boom = RuntimeError("anthropic is down")

    class _Failing:
        async def aclassify(self, inp):
            raise boom

    monkeypatch.setattr(engine, "_get_classifier", lambda: _Failing())

    with pytest.raises(RuntimeError) as caught:
        await engine.classify_user_message("Review my portfolio", [])

    assert caught.value is boom, "the real error must surface, not a fallback's"


def test_the_openai_fallback_is_gone():
    assert not hasattr(engine, "_classify_via_openai")
    assert not hasattr(engine, "_OPENAI_FUNCTION")
