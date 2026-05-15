from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from .document_generator import document_generation_chain
from .models import MacroSnapshot
from .prompts import (
    EXTRACT_MACRO_DATA_TOOL,
    EXTRACTION_SYSTEM_PROMPT_WEBSEARCH,
)

load_dotenv()

_CACHE_FILENAME = "market_commentary_latest.json"
_DOCUMENT_FILENAME = "market_commentary_latest.md"
_EXTRACTION_MODEL = "claude-haiku-4-5-20251001"
_EXTRACTION_MAX_TOKENS = 4096
_WEBSEARCH_MAX_USES = 25


# ---------------------------------------------------------------------------
# Private extraction schema — 14 indicator fields only.
# Excludes data_gaps and document_md so structured output schema is clean.
# ---------------------------------------------------------------------------


class _MacroExtraction(BaseModel):
    repo_rate_pct: Optional[float] = None
    rbi_stance: Optional[str] = None
    cpi_yoy_pct: Optional[float] = None
    nifty50_pe: Optional[float] = None
    nifty_midcap150_pe: Optional[float] = None
    nifty_smallcap250_pe: Optional[float] = None
    gsec_10yr_yield_pct: Optional[float] = None
    sbi_fd_1yr_rate_pct: Optional[float] = None
    gold_price_inr_per_10g: Optional[float] = None
    gold_price_usd_per_oz: Optional[float] = None
    fed_funds_rate_pct: Optional[float] = None
    fii_net_flows_cr_inr: Optional[float] = None
    brent_crude_usd: Optional[float] = None
    usd_inr_rate: Optional[float] = None


# ---------------------------------------------------------------------------
# Web-search extraction — Claude calls Anthropic's built-in web_search server tool,
# iterates as needed, and finalises via the extract_macro_data tool.
# Cost note: ~15-25 searches per run at $10/1000, plus Sonnet tokens.
# ---------------------------------------------------------------------------


def _to_snapshot(extraction: _MacroExtraction) -> MacroSnapshot:
    """Build a full MacroSnapshot from extracted data, tracking missing fields."""
    raw = extraction.model_dump()
    gaps = [k for k, v in raw.items() if v is None]
    return MacroSnapshot(**raw, data_gaps=gaps)


_extraction_llm = ChatAnthropic(
    model=_EXTRACTION_MODEL,
    max_tokens=_EXTRACTION_MAX_TOKENS,
).bind_tools(
    [
        {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": _WEBSEARCH_MAX_USES,
        },
        EXTRACT_MACRO_DATA_TOOL,
    ]
)


def run_websearch_extraction() -> MacroSnapshot:
    """Gather all 14 macro indicators via Claude + Anthropic web_search.

    Claude plans and issues its own searches, disambiguates known failure modes
    (SDF-vs-repo, gold USD/oz vs INR/10g, spot vs forward USD/INR), and returns a
    structured payload via the extract_macro_data tool. Returns an all-null
    MacroSnapshot if the model does not finalise (so the caller's cache-fallback
    path kicks in).
    """
    response = _extraction_llm.invoke([
        SystemMessage(content=EXTRACTION_SYSTEM_PROMPT_WEBSEARCH),
        HumanMessage(content=(
            "Gather current values for all 14 Indian macro indicators and "
            "return them via the extract_macro_data tool."
        )),
    ])

    for tool_call in response.tool_calls:
        if tool_call["name"] == "extract_macro_data":
            extraction = _MacroExtraction(**tool_call["args"])
            return _to_snapshot(extraction)

    # Model did not finalise — caller should rely on cache fallback.
    return MacroSnapshot(data_gaps=[])


# ---------------------------------------------------------------------------
# Cache manager — saves / loads MacroSnapshot for last-resort fallback
# ---------------------------------------------------------------------------


class CacheManager:
    """Persists the most recent successful MacroSnapshot to disk."""

    @staticmethod
    def save(snapshot: MacroSnapshot, cache_path: str) -> None:
        with open(cache_path, "w") as f:
            f.write(snapshot.model_dump_json(indent=2))

    @staticmethod
    def load(cache_path: str) -> Optional[MacroSnapshot]:
        if not os.path.exists(cache_path):
            return None
        try:
            with open(cache_path, "r") as f:
                data = json.load(f)
            return MacroSnapshot(**data)
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Market Commentary Agent (Part 1 — system-triggered daily pipeline)
# ---------------------------------------------------------------------------


class MarketCommentaryAgent:
    """Orchestrates the daily market commentary pipeline.

    1. Gathers 14 macro indicators via Claude + Anthropic web_search
    2. Writes the snapshot to ``market_commentary_latest.json``
    3. Generates a 2-page Markdown commentary and writes it to
       ``market_commentary_latest.md``

    This pipeline is intended to be triggered once daily by the system.

    Usage::

        agent = MarketCommentaryAgent()
        snapshot = agent.run()
        print(snapshot.nifty50_pe)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        output_dir: str = ".",
        generate_document: bool = True,
    ) -> None:
        if api_key:
            os.environ["ANTHROPIC_API_KEY"] = api_key
        self.output_dir = output_dir
        self._cache_path = os.path.join(output_dir, _CACHE_FILENAME)
        self._generate_document = generate_document

    def run_from_cache(
        self,
        max_age_sec: int,
        date: Optional[datetime] = None,
    ) -> Optional[str]:
        """Fast path: if the on-disk snapshot is fresh, skip scrape+extract.

        Loads the cached ``MacroSnapshot`` and returns the markdown commentary.
        If a rendered ``market_commentary_latest.md`` exists and is at least as
        fresh as the snapshot JSON, it is returned verbatim (no LLM call —
        the document is a deterministic function of the snapshot). Otherwise
        the document-generation chain is invoked and the result is persisted
        so the next call hits the fast path.

        Returns the markdown document if the cache is present, fresh, and
        non-empty; returns ``None`` otherwise (caller should run the full
        pipeline via :meth:`run`).
        """
        if not os.path.exists(self._cache_path):
            return None
        if (time.time() - os.path.getmtime(self._cache_path)) > max_age_sec:
            return None
        snapshot = CacheManager.load(self._cache_path)
        if snapshot is None or self._is_snapshot_empty(snapshot):
            return None

        md_path = os.path.join(self.output_dir, _DOCUMENT_FILENAME)
        if (
            os.path.exists(md_path)
            and os.path.getmtime(md_path) >= os.path.getmtime(self._cache_path)
        ):
            try:
                with open(md_path, "r") as f:
                    cached_md = f.read()
                if cached_md.strip():
                    return cached_md
            except OSError:
                pass  # fall through to regeneration

        regenerated_md = document_generation_chain.invoke(
            {"snapshot": snapshot, "date": date or datetime.utcnow()}
        )
        try:
            os.makedirs(self.output_dir, exist_ok=True)
            with open(md_path, "w") as f:
                f.write(regenerated_md)
        except OSError:
            pass
        return regenerated_md

    def run(self) -> MacroSnapshot:
        """Run the full pipeline: web-search extraction → cache → write JSON + Markdown.

        Returns:
            MacroSnapshot with the latest macro-economic indicator values.
        """
        # Step 1+2: Claude with web_search gathers and extracts all 14 indicators.
        snapshot = run_websearch_extraction()

        # Step 3: fallback to cache if all indicator fields are null
        if self._is_snapshot_empty(snapshot):
            cached = CacheManager.load(self._cache_path)
            if cached is not None:
                cached.data_gaps.append("ALL_LIVE_DATA_FAILED — using cached snapshot")
                snapshot = cached

        # Step 4: persist successful snapshot (also serves as the "latest" JSON output)
        os.makedirs(self.output_dir, exist_ok=True)
        if not self._is_snapshot_empty(snapshot):
            CacheManager.save(snapshot, self._cache_path)

        # Step 5: generate and write the 2-page Markdown commentary
        if self._generate_document:
            now = datetime.utcnow()
            document_md = document_generation_chain.invoke({"snapshot": snapshot, "date": now})
            snapshot.document_md = document_md
            self._write_document(document_md)

        return snapshot

    def _write_document(self, document_md: str) -> str:
        """Write the Markdown commentary to the fixed 'latest' path. Returns the path."""
        md_path = os.path.join(self.output_dir, _DOCUMENT_FILENAME)
        with open(md_path, "w") as f:
            f.write(document_md)
        return md_path

    @staticmethod
    def _is_snapshot_empty(snapshot: MacroSnapshot) -> bool:
        """Return True if every indicator field is None."""
        fields = [
            snapshot.repo_rate_pct, snapshot.rbi_stance, snapshot.cpi_yoy_pct,
            snapshot.nifty50_pe, snapshot.nifty_midcap150_pe, snapshot.nifty_smallcap250_pe,
            snapshot.gsec_10yr_yield_pct, snapshot.sbi_fd_1yr_rate_pct,
            snapshot.gold_price_inr_per_10g, snapshot.gold_price_usd_per_oz,
            snapshot.fed_funds_rate_pct, snapshot.fii_net_flows_cr_inr,
            snapshot.brent_crude_usd, snapshot.usd_inr_rate,
        ]
        return all(f is None for f in fields)
