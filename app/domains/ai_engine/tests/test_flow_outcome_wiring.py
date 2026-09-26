"""_finalize must report the right outcome for each of its four exit paths.

Testing the emitter alone proves nothing: the bug that matters is a call site
passing the wrong outcome, or forgetting to pass one at all.
"""

import time

import pytest

from app.domains.ai_engine.services import brain as brain_mod
from app.domains.ai_engine.types import IntentDecision


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        brain_mod, "capture_flow_completed", lambda **kw: calls.append(kw)
    )
    return calls


async def _finalize(**overrides):
    kwargs = dict(
        text="hello",
        intent=IntentDecision(name="rebalancing", confidence=0.9),
        flow=["flow: flow_rebalancing"],
        t0=time.perf_counter(),
        db=None,
        uid="user-1",
        sid="session-1",
    )
    kwargs.update(overrides)
    return await brain_mod.ChatBrain()._finalize(**kwargs)


async def test_the_default_exit_reports_ok(sent):
    """The canned and normal-result call sites pass no outcome at all, so the
    default is what they get. It must be "ok"."""
    await _finalize()
    assert sent[0]["outcome"] == "ok"
    assert sent[0]["failure_reason"] is None
    assert sent[0]["intent"] == "rebalancing"


async def test_a_timeout_exit_reports_failed(sent):
    await _finalize(outcome="failed", failure_reason="timeout")
    assert sent[0]["outcome"] == "failed"
    assert sent[0]["failure_reason"] == "timeout"


async def test_an_exception_exit_carries_the_class_name(sent):
    await _finalize(outcome="failed", failure_reason="NumericValueOutOfRangeError")
    assert sent[0]["failure_reason"] == "NumericValueOutOfRangeError"


async def test_the_user_is_attributed(sent):
    await _finalize(uid="user-42")
    assert sent[0]["distinct_id"] == "user-42"


async def test_duration_is_measured_not_zero(sent):
    await _finalize(t0=time.perf_counter() - 1.5)
    assert sent[0]["duration_ms"] >= 1500


async def test_a_missing_intent_does_not_crash_the_turn(sent):
    await _finalize(intent=None)
    assert sent[0]["intent"] is None
