"""AI bridge — `market_commentary_service.py`.

Sits between FastAPI services/routers and the ``AI_Agents/src`` packages (added to `sys.path` via ``ensure_ai_agents_path``). Handles env keys, async/thread boundaries, and user-context mapping. Ideal mutual fund allocation is invoked from here using ``Ideal_asset_allocation`` inside the app layer (e.g. ``ideal_allocation_runner``) so `AI_Agents` files stay untouched.
"""


from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

import httpx

from app.config import get_settings
from app.services.ai_bridge.common import ensure_ai_agents_path

ensure_ai_agents_path()

from market_commentary.agent import MarketCommentaryAgent
from market_commentary.document_generator import DocumentGenerator
from market_commentary.models import MacroSnapshot
from market_commentary.prompts import (
    DOCUMENT_GENERATION_SYSTEM_PROMPT,
    EXTRACTION_SYSTEM_PROMPT,
    EXTRACT_MACRO_DATA_TOOL,
)
from market_commentary.scraper import IndicatorScraper

logger = logging.getLogger(__name__)

_IST = __import__("zoneinfo").ZoneInfo("Asia/Kolkata")

# Reuse Agent cache: skip 14× web search + Haiku extraction when snapshot is recent.
_CACHE_MAX_AGE_SEC = int(os.getenv("MARKET_COMMENTARY_CACHE_MAX_AGE_SEC", "3600"))


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _macro_cache_path() -> Path:
    return _backend_root() / "market_commentary_cache.json"


def _try_commentary_from_fresh_cache(api_key: str | None) -> str | None:
    """
    If market_commentary_cache.json exists and is newer than _CACHE_MAX_AGE_SEC, load the
    MacroSnapshot and run only DocumentGenerator (Sonnet). This avoids DDG + Haiku, which
    usually exceed the chat-layer timeout.
    """
    path = _macro_cache_path()
    if not path.is_file():
        return None
    try:
        age = time.time() - path.stat().st_mtime
        if age > _CACHE_MAX_AGE_SEC:
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("document_md", None)
        snapshot = MacroSnapshot.model_validate(raw)
        if MarketCommentaryAgent._is_snapshot_empty(snapshot):
            return None
        logger.info(
            "Market commentary fast path: cached macro snapshot (age %.0f min), document gen only",
            age / 60.0,
        )
        gen = DocumentGenerator(api_key=api_key)
        return gen.generate(snapshot, date=datetime.now(_IST))
    except Exception as exc:
        logger.debug("Market commentary cache fast path skipped: %s", exc)
        return None


def _convert_extraction_tool_for_openai() -> dict:
    """Convert the Anthropic EXTRACT_MACRO_DATA_TOOL schema to OpenAI function format."""
    return {
        "type": "function",
        "function": {
            "name": EXTRACT_MACRO_DATA_TOOL["name"],
            "description": EXTRACT_MACRO_DATA_TOOL["description"],
            "parameters": EXTRACT_MACRO_DATA_TOOL["input_schema"],
        },
    }


async def _extract_via_openai(raw_snippets: dict[str, str]) -> MacroSnapshot:
    """Step 1 of MC pipeline via OpenAI: extract structured data from snippets."""
    api_key = os.getenv("OPENAI_API_KEY")
    parts = [
        "Below are raw web-search snippets for various Indian market "
        "indicators. Extract the most recent numeric value for each.\n"
    ]
    for name, text in raw_snippets.items():
        body = text.strip() if text.strip() else "(no search results)"
        parts.append(f"### {name}\n{body}")
    user_content = "\n\n".join(parts)

    payload = {
        "model": "gpt-4o-mini",
        "max_tokens": 512,
        "messages": [
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "tools": [_convert_extraction_tool_for_openai()],
        "tool_choice": {"type": "function", "function": {"name": "extract_macro_data"}},
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
        resp.raise_for_status()

    data = resp.json()
    raw = json.loads(data["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"])
    data_gaps = [k for k, v in raw.items() if v is None]
    return MacroSnapshot(**raw, data_gaps=data_gaps)


async def _generate_document_via_openai(snapshot: MacroSnapshot) -> str:
    """Step 2 of MC pipeline via OpenAI: generate commentary using existing prompts."""
    api_key = os.getenv("OPENAI_API_KEY")
    user_prompt = DocumentGenerator._build_user_prompt(snapshot, datetime.now(_IST))

    payload = {
        "model": "gpt-4o-mini",
        "max_tokens": 4096,
        "messages": [
            {"role": "system", "content": DOCUMENT_GENERATION_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
        resp.raise_for_status()

    data = resp.json()
    return data["choices"][0]["message"]["content"]


async def generate_market_commentary(
    user_question: str,
    conversation_history: list[dict[str, str]] | None = None,
) -> str:
    """Run the MarketCommentaryAgent pipeline (Anthropic → OpenAI fallback)."""
    del user_question, conversation_history  # reserved for future question-conditioned runs

    mc_key = get_settings().get_anthropic_market_commentary_key()

    # --- Fast path: recent on-disk snapshot → only Sonnet document (no web scrape) ---
    try:
        doc = await asyncio.to_thread(_try_commentary_from_fresh_cache, mc_key)
        if doc:
            return doc
    except Exception as exc:
        logger.debug("Market commentary fast path error: %s", exc)

    # --- Attempt 1: Full Anthropic pipeline (writes cache under backend root) ---
    try:
        agent = MarketCommentaryAgent(
            api_key=mc_key,
            generate_document=True,
            output_dir=str(_backend_root()),
        )
        snapshot = await asyncio.to_thread(agent.run)
        if snapshot.document_md:
            return snapshot.document_md
    except Exception as exc:
        logger.warning("Anthropic MC agent failed (%s), trying OpenAI fallback...", exc)

    # --- Attempt 2: Same pipeline, same prompts, via OpenAI ---
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    scraper = IndicatorScraper(max_workers=7)
    raw_snippets = await asyncio.to_thread(scraper.scrape_all)
    snapshot = await _extract_via_openai(raw_snippets)
    return await _generate_document_via_openai(snapshot)
