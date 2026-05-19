"""Bridge: orchestrate the market-commentary agent for the FastAPI chat layer.

Pipeline (Anthropic-only): fast path (fresh cached snapshot → document-gen only)
→ full ``MarketCommentaryAgent`` run (websearch → extract → cache → doc gen).
Path resolution and cache-freshness config live here; all domain logic lives in
``AI_Agents/src/market_commentary``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import zoneinfo
from datetime import datetime
from pathlib import Path

from app.config import get_settings
from app.services.ai_bridge.common import ensure_ai_agents_path

ensure_ai_agents_path()

import market_commentary
from market_commentary.main import MarketCommentaryAgent

logger = logging.getLogger(__name__)

_IST = zoneinfo.ZoneInfo("Asia/Kolkata")

# Skip 14x web search + Sonnet extraction when the on-disk snapshot is still fresh.
_CACHE_MAX_AGE_SEC = int(os.getenv("MARKET_COMMENTARY_CACHE_MAX_AGE_SEC", "86400"))

_MARKET_COMMENTARY_DIR = Path(market_commentary.__file__).resolve().parents[2] / "Reference_docs"


async def generate_market_commentary(
    user_question: str,
    conversation_history: list[dict[str, str]] | None = None,
) -> str:
    """Run the MarketCommentaryAgent pipeline (cache fast path -> full run)."""
    del user_question, conversation_history  # reserved for future question-conditioned runs

    mc_key = get_settings().get_anthropic_market_commentary_key()
    agent = MarketCommentaryAgent(
        api_key=mc_key,
        output_dir=str(_MARKET_COMMENTARY_DIR),
        generate_document=True,
    )

    try:
        doc = await asyncio.to_thread(
            agent.run_from_cache, _CACHE_MAX_AGE_SEC, datetime.now(_IST)
        )
        if doc:
            return doc
    except Exception as exc:
        logger.debug("Market commentary fast path skipped: %s", exc)

    snapshot = await asyncio.to_thread(agent.run)
    if not snapshot.document_md:
        raise RuntimeError("Market commentary pipeline returned an empty document.")
    return snapshot.document_md
