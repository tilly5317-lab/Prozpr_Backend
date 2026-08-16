"""Fund-house view module — serves Prozpr's monthly market stance.

Unlike the factual ``market_commentary`` module (web-search + extraction +
cache), this is a plain read of the hand-maintained ``fund_house_commentry.md``
in ``Reference_docs``. A missing or empty file yields ``payload=None`` so the
sequence degrades gracefully to a factual / general answer — the view is
optional by design and its freshness is owned by whoever updates the file.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.ai_engine.types import ModuleOutput

ensure_ai_agents_path()

import market_commentary  # noqa: E402  (resolves the shared Reference_docs dir)
from common import read_text_bom_aware  # noqa: E402  (BOM-safe: the .md may be UTF-16 from PowerShell)

logger = logging.getLogger(__name__)

_VIEW_PATH = (
    Path(market_commentary.__file__).resolve().parents[2]
    / "Reference_docs"
    / "fund_house_commentry.md"
)


def _load_view() -> str | None:
    if not _VIEW_PATH.exists():
        logger.info("Fund-house view file absent at %s; continuing without it", _VIEW_PATH)
        return None
    text = read_text_bom_aware(_VIEW_PATH).strip()
    return text or None


async def run(turn, ctx, prior) -> ModuleOutput:  # noqa: ARG001 — uniform module signature
    """Return the fund-house view markdown, or ``None`` if unavailable."""
    return ModuleOutput(payload=_load_view())


__all__ = ["run"]
