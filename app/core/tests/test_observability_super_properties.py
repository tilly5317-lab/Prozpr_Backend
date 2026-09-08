"""Every backend event must be attributable to a service, env and version."""

from unittest.mock import MagicMock, patch

from app.core import observability


def test_init_posthog_sets_super_properties(monkeypatch):
    monkeypatch.setattr(observability, "_posthog_client", None)
    fake_settings = MagicMock()
    fake_settings.get_posthog_api_key.return_value = "phc_test"
    fake_settings.get_posthog_host.return_value = "https://us.i.posthog.com"
    fake_settings.posthog_llm_capture_content.return_value = False
    fake_settings.DEPLOY_ENV = "staging"

    with patch("app.core.config.get_settings", return_value=fake_settings):
        with patch("posthog.Posthog") as ctor:
            observability.init_posthog()

    kwargs = ctor.call_args.kwargs
    sp = kwargs["super_properties"]
    assert sp["service"] == "prozpr-backend"
    assert sp["environment"] == "staging"
    assert "service_version" in sp
    monkeypatch.setattr(observability, "_posthog_client", None)


def test_domain_survives_the_super_properties_merge(monkeypatch):
    """Super properties merge SECOND (posthog/client.py:1666) and overwrite
    same-named event properties.

    A MagicMock client cannot catch this — it records what we passed, not what
    the SDK would send. That blind spot shipped a bug that flattened 63,595
    events onto one `service` value for five days. So: a real client, captured
    via before_send, which fires after the merge and drops the event when it
    returns None (nothing leaves the process).
    """
    from posthog import Posthog

    captured: list[dict] = []
    client = Posthog(
        "phc_test",
        host="https://example.invalid",
        super_properties={"service": "prozpr-backend", "environment": "test"},
        before_send=lambda msg: captured.append(msg) or None,
    )
    monkeypatch.setattr(observability, "_posthog_client", client)
    try:
        observability.capture_http_request(
            status_code=200,
            path="/api/v1/goals/{goal_id}",
            method="GET",
            duration_ms=12.0,
        )
    finally:
        monkeypatch.setattr(observability, "_posthog_client", None)

    assert captured, "before_send never fired — the event never reached the merge"
    props = captured[0]["properties"]
    assert props["domain"] == "goals"
    assert props["service"] == "prozpr-backend"


def test_service_version_falls_back_to_unknown(monkeypatch):
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    assert observability._service_version() == "unknown"


def test_service_version_is_short_sha(monkeypatch):
    monkeypatch.setenv("GIT_COMMIT", "cf19e3131e6a4b7c9d0f2a5b8c1d4e7f0a3b6c9d")
    assert observability._service_version() == "cf19e3131e6a"
