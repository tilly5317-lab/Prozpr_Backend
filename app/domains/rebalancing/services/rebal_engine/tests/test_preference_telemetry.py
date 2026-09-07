"""preference_unserved: content-free, never raises, no-ops without a client."""

import app.core.observability as obs


class _FakeClient:
    def __init__(self):
        self.events = []

    def capture(self, event, distinct_id=None, properties=None):
        self.events.append((event, distinct_id, properties))


def test_capture_is_noop_without_client(monkeypatch):
    monkeypatch.setattr(obs, "_posthog_client", None)
    obs.capture_preference_unserved(flow="rebalancing", failure_class="redirect",
                                    session_id="s1", distinct_id="u1")  # must not raise


def test_capture_sends_ids_and_tokens_only(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(obs, "_posthog_client", fake)
    obs.capture_preference_unserved(flow="rebalancing", failure_class="fund_unknown",
                                    session_id="sess-42", distinct_id="user-7")
    ((event, distinct_id, props),) = fake.events
    assert event == "preference_unserved"
    assert distinct_id == "user-7"
    assert props == {"flow": "rebalancing", "failure_class": "fund_unknown",
                     "session_id": "sess-42"}
