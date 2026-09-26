"""init_metrics: off without a key, bounded series, flushed on shutdown."""

import pytest

from app.core import otel


@pytest.fixture(autouse=True)
def _reset():
    yield
    otel.shutdown_otel()


def test_disabled_without_a_posthog_key(monkeypatch):
    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: type("S", (), {"get_posthog_api_key": lambda self: ""})(),
    )
    assert otel.init_metrics() is None
    assert otel.get_meter_provider() is None


def test_every_configured_metric_name_is_actually_supported():
    """An unsupported key is SILENTLY IGNORED by the instrumentor.

    A "system.disk.usage" entry looked correct, passed review, and produced no
    disk metric whatsoever — caught only by checking the live pipeline. This
    pins each key against the instrumentor's own table so the next typo fails
    here instead of in production.
    """
    from opentelemetry.instrumentation.system_metrics import _DEFAULT_CONFIG

    unsupported = set(otel._SYSTEM_METRICS) - set(_DEFAULT_CONFIG)
    assert not unsupported, f"silently ignored by the instrumentor: {unsupported}"


def test_metric_set_stays_small():
    """A series is one unique attribute combination; the default config explodes.

    Note system.cpu.utilization is per-core (percpu=True + a `cpu` label), so its
    real series count is cores x states — the reason this budget is not just a
    label count.
    """
    assert "system.swap.usage" not in otel._SYSTEM_METRICS
    assert not any(k.startswith("system.network") for k in otel._SYSTEM_METRICS)
    assert not any(k.startswith("process.runtime") for k in otel._SYSTEM_METRICS), (
        "process.runtime.* is deprecated in the instrumentor"
    )
    assert len(otel._SYSTEM_METRICS) <= 6


def test_export_interval_is_a_minute():
    assert otel._METRIC_EXPORT_INTERVAL_MILLIS == 60_000


def test_shutdown_flushes_and_clears_the_meter_provider(monkeypatch):
    flushed = []

    class _FakeProvider:
        def shutdown(self):
            flushed.append(True)

    monkeypatch.setattr(otel, "_meter_provider", _FakeProvider())
    otel.shutdown_otel()
    assert flushed == [True], "meter provider must be flushed with the others"
    assert otel.get_meter_provider() is None
