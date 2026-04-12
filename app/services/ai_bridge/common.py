"""AI bridge — `common.py`.

Sits between FastAPI services/routers and the ``AI_Agents/src`` packages (added to `sys.path` via ``ensure_ai_agents_path``). Handles env keys, async/thread boundaries, and user-context mapping. Ideal mutual fund allocation is invoked from here using ``Ideal_asset_allocation`` inside the app layer (e.g. ``ideal_allocation_runner``) so `AI_Agents` files stay untouched.
"""


from __future__ import annotations

import sys
from pathlib import Path


def ensure_ai_agents_path() -> None:
    backend_root = Path(__file__).resolve().parents[3]
    src = backend_root / "AI_Agents" / "src"
    s = str(src)
    if s not in sys.path:
        sys.path.insert(0, s)


def build_history_block(history: list[dict[str, str]] | None) -> str:
    if not history:
        return ""
    recent = history[-6:]
    lines = ["--- Recent Conversation History ---"]
    for msg in recent:
        label = "Customer" if msg["role"] == "user" else "AILAX"
        lines.append(f"{label}: {msg['content']}")
    lines.append("---")
    return "\n".join(lines)
