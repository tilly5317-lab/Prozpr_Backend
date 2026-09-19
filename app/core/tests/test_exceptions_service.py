"""`_service_from_path` derives the domain/service from a request path, so 5xx
errors can be grouped by the service that raised them on the PostHog dashboard."""

from __future__ import annotations

from app.core.exceptions import _service_from_path


def test_service_from_path_extracts_domain_after_v1() -> None:
    assert _service_from_path("/api/v1/mf-ingest/cams-pdf") == "mf-ingest"
    assert _service_from_path("/api/v1/chat/message") == "chat"
    assert _service_from_path("/api/v1/health") == "health"


def test_service_from_path_handles_edge_cases() -> None:
    assert _service_from_path("/") == "unknown"
    assert _service_from_path("/docs") == "docs"
