"""OTel bootstrap: no-op without a token, correct endpoints with one."""

from unittest.mock import MagicMock, patch

from app.core import otel


def test_init_otel_is_noop_without_token(monkeypatch):
    otel._tracer_provider = None
    otel._logger_provider = None
    fake = MagicMock()
    fake.get_posthog_api_key.return_value = ""
    with patch("app.core.config.get_settings", return_value=fake):
        tp, lp = otel.init_otel()
    assert tp is None and lp is None


def test_init_otel_builds_posthog_otlp_endpoints(monkeypatch):
    otel._tracer_provider = None
    otel._logger_provider = None
    fake = MagicMock()
    fake.get_posthog_api_key.return_value = "phc_test"
    fake.get_posthog_host.return_value = "https://us.i.posthog.com"
    fake.DEPLOY_ENV = "production"

    with patch("app.core.config.get_settings", return_value=fake):
        with patch("app.core.otel.OTLPSpanExporter") as span_exp:
            with patch("app.core.otel.OTLPLogExporter") as log_exp:
                tp, lp = otel.init_otel()

    assert tp is not None and lp is not None
    assert span_exp.call_args.kwargs["endpoint"] == "https://us.i.posthog.com/i/v1/traces"
    assert log_exp.call_args.kwargs["endpoint"] == "https://us.i.posthog.com/i/v1/logs"
    assert span_exp.call_args.kwargs["headers"]["Authorization"] == "Bearer phc_test"
    otel.shutdown_otel()


def test_export_timeout_fits_inside_the_pm2_kill_window():
    """A flush that outlives PM2's kill_timeout is a flush that gets SIGKILLed.

    The SDK default is 30s; ecosystem.config.cjs allows 8s (Task 11). Anything
    at or above that window silently loses whatever is still buffered.
    """
    assert otel._EXPORT_TIMEOUT_MILLIS <= 5000
