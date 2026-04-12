from __future__ import annotations

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

_IST = ZoneInfo("Asia/Kolkata")
from typing import Dict, List, Optional

import anthropic
from dotenv import load_dotenv

from .document_generator import DocumentGenerator
from .models import MacroSnapshot
from .prompts import EXTRACT_MACRO_DATA_TOOL, EXTRACTION_SYSTEM_PROMPT
from .scraper import IndicatorScraper

load_dotenv()

# Path for the cache file (relative to output_dir)
_CACHE_FILENAME = "market_commentary_cache.json"


# ---------------------------------------------------------------------------
# Cache manager — saves / loads MacroSnapshot for last-resort fallback
# ---------------------------------------------------------------------------


class CacheManager:
    """Persists the most recent successful MacroSnapshot to disk."""

    @staticmethod
    def save(snapshot: MacroSnapshot, cache_path: str) -> None:
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(snapshot.model_dump_json(indent=2))

    @staticmethod
    def load(cache_path: str) -> Optional[MacroSnapshot]:
        if not os.path.exists(cache_path):
            return None
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return MacroSnapshot(**data)
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Market Commentary Agent (extraction phase)
# ---------------------------------------------------------------------------


class MarketCommentaryAgent:
    """Fetches Indian macro indicators via web search and extracts
    structured data using Claude Haiku.

    Architecture:
        1. DuckDuckGo web search for 14 indicators (concurrent)
        2. Claude Haiku extracts numeric values from snippets (1 API call)
        3. MacroSnapshot written to a timestamped JSON file

    Usage::

        agent = MarketCommentaryAgent()
        snapshot = agent.run()
        print(snapshot.nifty50_pe)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-haiku-4-5-20251001",
        output_dir: str = ".",
        max_workers: int = 7,
        generate_document: bool = True,
    ) -> None:
        resolved_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.client = anthropic.Anthropic(api_key=resolved_key)
        self.model = model
        self.output_dir = output_dir
        self._scraper = IndicatorScraper(max_workers=max_workers)
        self._cache_path = os.path.join(output_dir, _CACHE_FILENAME)
        self._generate_document = generate_document
        if generate_document:
            self._doc_generator = DocumentGenerator(api_key=resolved_key)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> MacroSnapshot:
        """Run the full extraction pipeline: search → extract → write JSON.

        Returns:
            MacroSnapshot with the latest macro-economic indicator values.

        Raises:
            ValueError: If Claude does not return the expected tool-use block.
        """
        # Step 1: web-search all indicators concurrently
        raw_snippets = self._scraper.scrape_all()

        # Step 2: extract structured data via Claude Haiku
        snapshot = self._extract_data(raw_snippets)

        # Fallback: if snapshot is entirely empty, try cache
        if self._is_snapshot_empty(snapshot):
            cached = CacheManager.load(self._cache_path)
            if cached is not None:
                cached.data_gaps.append("ALL_LIVE_DATA_FAILED — using cached snapshot")
                snapshot = cached

        # Cache successful extraction for future fallback
        if not self._is_snapshot_empty(snapshot):
            CacheManager.save(snapshot, self._cache_path)

        # Step 3: write JSON to disk
        ts = datetime.now(_IST).strftime("%Y%m%d_%H%M%S")
        json_path = self._write_output(snapshot, ts=ts)

        # Step 4: generate and write the 2-page Markdown fund document
        if self._generate_document:
            now = datetime.strptime(ts, "%Y%m%d_%H%M%S")
            document_md = self._doc_generator.generate(snapshot, date=now)
            snapshot.document_md = document_md
            self._write_document(document_md, json_path)

        return snapshot

    # ------------------------------------------------------------------
    # Extraction (Claude Haiku)
    # ------------------------------------------------------------------

    def _extract_data(self, raw_snippets: Dict[str, str]) -> MacroSnapshot:
        """Pass all search snippets to Claude Haiku and extract numbers."""
        user_content = self._format_snippets_for_extraction(raw_snippets)

        response = self.client.messages.create(
            model=self.model,
            max_tokens=512,
            system=EXTRACTION_SYSTEM_PROMPT,
            tools=[EXTRACT_MACRO_DATA_TOOL],
            tool_choice={"type": "tool", "name": "extract_macro_data"},
            messages=[{"role": "user", "content": user_content}],
        )

        tool_block = next(
            (b for b in response.content if b.type == "tool_use"), None
        )
        if tool_block is None:
            raise ValueError(
                "Claude Haiku did not return an extract_macro_data tool block. "
                f"Full response: {response}"
            )

        raw: dict = tool_block.input

        # Identify data gaps (fields that came back as None)
        data_gaps: List[str] = [key for key, val in raw.items() if val is None]

        return MacroSnapshot(**raw, data_gaps=data_gaps)

    @staticmethod
    def _format_snippets_for_extraction(snippets: Dict[str, str]) -> str:
        """Format raw search snippets into a labelled block for Claude."""
        parts: List[str] = [
            "Below are raw web-search snippets for various Indian market "
            "indicators. Extract the most recent numeric value for each.\n"
        ]
        for name, text in snippets.items():
            body = text.strip() if text.strip() else "(no search results)"
            parts.append(f"### {name}\n{body}")
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Write output
    # ------------------------------------------------------------------

    def _write_output(self, snapshot: MacroSnapshot, ts: Optional[str] = None) -> str:
        """Write the MacroSnapshot to a timestamped JSON file. Returns the path."""
        os.makedirs(self.output_dir, exist_ok=True)
        if ts is None:
            ts = datetime.now(_IST).strftime("%Y%m%d_%H%M%S")
        path = os.path.join(self.output_dir, f"macro_snapshot_{ts}.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(snapshot.model_dump_json(indent=2))
        return path

    def _write_document(self, document_md: str, json_path: str) -> str:
        """Write the Markdown commentary alongside the JSON file. Returns the path."""
        md_path = json_path.replace(".json", ".md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(document_md)
        return md_path

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_snapshot_empty(snapshot: MacroSnapshot) -> bool:
        """Return True if every indicator field is None."""
        fields = [
            snapshot.repo_rate_pct,
            snapshot.rbi_stance,
            snapshot.cpi_yoy_pct,
            snapshot.nifty50_pe,
            snapshot.nifty_midcap150_pe,
            snapshot.nifty_smallcap250_pe,
            snapshot.gsec_10yr_yield_pct,
            snapshot.sbi_fd_1yr_rate_pct,
            snapshot.gold_price_inr_per_10g,
            snapshot.gold_price_usd_per_oz,
            snapshot.fed_funds_rate_pct,
            snapshot.fii_net_flows_cr_inr,
            snapshot.brent_crude_usd,
            snapshot.usd_inr_rate,
        ]
        return all(f is None for f in fields)
