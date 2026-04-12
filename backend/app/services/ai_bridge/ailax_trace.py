"""AI bridge — `ailax_trace.py`.

Sits between FastAPI services/routers and the ``AI_Agents/src`` packages (added to `sys.path` via ``ensure_ai_agents_path``). Handles env keys, async/thread boundaries, and user-context mapping. Ideal mutual fund allocation is invoked from here using ``Ideal_asset_allocation`` inside the app layer (e.g. ``ideal_allocation_runner``) so `AI_Agents` files stay untouched.
"""


from __future__ import annotations

_PREFIX = "[AILAX_TRACE]"


def trace_line(message: str) -> None:
    print(f"{_PREFIX} {message}", flush=True)


def trace_response_preview(label: str, text: str, max_chars: int = 600) -> None:
    t = (text or "").strip().replace("\n", " ")
    if len(t) > max_chars:
        t = t[:max_chars] + "…"
    trace_line(f"{label} (preview): {t}")
