from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Dict, List, Optional

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel

from .document_generator import document_generation_chain
from .models import MacroSnapshot
from .prompts import EXTRACTION_PROMPT
from .scraper import IndicatorScraper

load_dotenv()

_CACHE_FILENAME = "market_commentary_cache.json"
_EXTRACTION_MODEL = "claude-sonnet-4-6"
_EXTRACTION_MAX_TOKENS = 1024


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
# LCEL extraction chain: snippets → MacroSnapshot
# ---------------------------------------------------------------------------


def _format_snippets(snippets: Dict[str, str]) -> dict:
    """Format raw snippet dict into the template variable for EXTRACTION_PROMPT."""
    parts: List[str] = [
        "Below are raw web-search snippets for various Indian market "
        "indicators. Extract the most recent numeric value for each.\n"
    ]
    for name, text in snippets.items():
        body = text.strip() if text.strip() else "(no search results)"
        parts.append(f"### {name}\n{body}")
    return {"formatted_snippets": "\n\n".join(parts)}


def _to_snapshot(extraction: _MacroExtraction) -> MacroSnapshot:
    """Build a full MacroSnapshot from extracted data, tracking missing fields."""
    raw = extraction.model_dump()
    gaps = [k for k, v in raw.items() if v is None]
    return MacroSnapshot(**raw, data_gaps=gaps)


_sonnet = ChatAnthropic(model=_EXTRACTION_MODEL, max_tokens=_EXTRACTION_MAX_TOKENS)
_extraction_llm = _sonnet.with_structured_output(_MacroExtraction)

extraction_chain = (
    RunnableLambda(_format_snippets)
    | EXTRACTION_PROMPT
    | _extraction_llm
    | RunnableLambda(_to_snapshot)
)


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

    1. Scrapes 14 macro indicators via Tavily (concurrent)
    2. Extracts structured data using Claude Sonnet via LangChain
    3. Writes a timestamped JSON snapshot to disk
    4. Generates a 2-page Markdown commentary and writes it to disk

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
        max_workers: int = 7,
        generate_document: bool = True,
    ) -> None:
        if api_key:
            os.environ["ANTHROPIC_API_KEY"] = api_key
        self.output_dir = output_dir
        self._scraper = IndicatorScraper(max_workers=max_workers)
        self._cache_path = os.path.join(output_dir, _CACHE_FILENAME)
        self._generate_document = generate_document

    def run(self) -> MacroSnapshot:
        """Run the full pipeline: scrape → extract → cache → write JSON + Markdown.

        Returns:
            MacroSnapshot with the latest macro-economic indicator values.
        """
        # Step 1: scrape all indicators concurrently via Tavily
        raw_snippets = self._scraper.scrape_all()

        # Step 2: extract structured data via LCEL chain (Claude Sonnet)
        snapshot = extraction_chain.invoke(raw_snippets)

        # Step 3: fallback to cache if all indicator fields are null
        if self._is_snapshot_empty(snapshot):
            cached = CacheManager.load(self._cache_path)
            if cached is not None:
                cached.data_gaps.append("ALL_LIVE_DATA_FAILED — using cached snapshot")
                snapshot = cached

        # Step 4: persist successful snapshot to cache
        if not self._is_snapshot_empty(snapshot):
            CacheManager.save(snapshot, self._cache_path)

        # Step 5: write timestamped JSON
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        json_path = self._write_output(snapshot, ts=ts)

        # Step 6: generate and write the 2-page Markdown commentary
        if self._generate_document:
            now = datetime.strptime(ts, "%Y%m%d_%H%M%S")
            document_md = document_generation_chain.invoke({"snapshot": snapshot, "date": now})
            snapshot.document_md = document_md
            self._write_document(document_md, json_path)

        return snapshot

    def _write_output(self, snapshot: MacroSnapshot, ts: Optional[str] = None) -> str:
        """Write the MacroSnapshot to a timestamped JSON file. Returns the path."""
        os.makedirs(self.output_dir, exist_ok=True)
        if ts is None:
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(self.output_dir, f"macro_snapshot_{ts}.json")
        with open(path, "w") as f:
            f.write(snapshot.model_dump_json(indent=2))
        return path

    def _write_document(self, document_md: str, json_path: str) -> str:
        """Write the Markdown commentary alongside the JSON file. Returns the path."""
        md_path = json_path.replace(".json", ".md")
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
