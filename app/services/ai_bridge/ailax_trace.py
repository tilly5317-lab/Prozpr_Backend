"""Lightweight stdout tracing prefixed with [AILAX_TRACE] for server-side debugging."""


from __future__ import annotations

_PREFIX = "[AILAX_TRACE]"


def trace_line(message: str) -> None:
    print(f"{_PREFIX} {message}", flush=True)


def trace_response_preview(label: str, text: str, max_chars: int = 600) -> None:
    t = (text or "").strip().replace("\n", " ")
    if len(t) > max_chars:
        t = t[:max_chars] + "…"
    trace_line(f"{label} (preview): {t}")
