"""Fund-house view module (market side) — serves the WHOLE multi-house view.

Delegates to the shared ``house_view`` slicer with ``prozpr_only=False`` so market
questions get every fund house (the advice paths use ``prozpr_only=True`` elsewhere).
A missing / empty / invalid file yields ``payload=None`` so the sequence degrades
gracefully to a factual / general answer — the view is optional by design.
"""

from __future__ import annotations

from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.ai_engine.types import ModuleOutput

ensure_ai_agents_path()

from house_view import load_house_view  # noqa: E402  (bare import via ensure_ai_agents_path)


async def run(turn, ctx, prior) -> ModuleOutput:  # noqa: ARG001 — uniform module signature
    """Return the whole multi-house view markdown, or ``None`` if unavailable."""
    return ModuleOutput(payload=load_house_view(prozpr_only=False))


__all__ = ["run"]
