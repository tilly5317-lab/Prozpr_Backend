"""
Unit tests for MarketCommentaryAgent (Part 1) and answer_question (Part 2).

All tests mock LangChain chains and Tavily — no live API calls or
network requests are made.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Minimal stubs so the module can be imported without installed packages
# ---------------------------------------------------------------------------


def _stub_langchain():
    """Stub all langchain packages used by the market_commentary module."""
    # langchain_anthropic
    if "langchain_anthropic" not in sys.modules:
        pkg = types.ModuleType("langchain_anthropic")
        pkg.ChatAnthropic = MagicMock
        sys.modules["langchain_anthropic"] = pkg

    # langchain_core (parent)
    if "langchain_core" not in sys.modules:
        sys.modules["langchain_core"] = types.ModuleType("langchain_core")

    # langchain_core.prompts
    if "langchain_core.prompts" not in sys.modules:
        sub = types.ModuleType("langchain_core.prompts")
        sub.ChatPromptTemplate = MagicMock
        sys.modules["langchain_core.prompts"] = sub
        sys.modules["langchain_core"].prompts = sub  # type: ignore[attr-defined]

    # langchain_core.output_parsers
    if "langchain_core.output_parsers" not in sys.modules:
        sub = types.ModuleType("langchain_core.output_parsers")
        sub.StrOutputParser = MagicMock
        sys.modules["langchain_core.output_parsers"] = sub
        sys.modules["langchain_core"].output_parsers = sub  # type: ignore[attr-defined]

    # langchain_core.runnables
    if "langchain_core.runnables" not in sys.modules:
        sub = types.ModuleType("langchain_core.runnables")
        sub.RunnableLambda = MagicMock
        sys.modules["langchain_core.runnables"] = sub
        sys.modules["langchain_core"].runnables = sub  # type: ignore[attr-defined]


def _stub_dotenv():
    if "dotenv" not in sys.modules:
        pkg = types.ModuleType("dotenv")
        pkg.load_dotenv = lambda: None
        sys.modules["dotenv"] = pkg


def _stub_tavily():
    if "tavily" not in sys.modules:
        pkg = types.ModuleType("tavily")
        pkg.TavilyClient = MagicMock
        sys.modules["tavily"] = pkg


_stub_langchain()
_stub_dotenv()
_stub_tavily()


# ---------------------------------------------------------------------------
# Now import the modules under test
# ---------------------------------------------------------------------------

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from market_commentary import MarketCommentaryAgent, MacroSnapshot  # noqa: E402
from market_commentary.main import CacheManager  # noqa: E402
from market_commentary.scraper import IndicatorScraper  # noqa: E402
from market_commentary.chat_qa import answer_question, load_latest_commentary  # noqa: E402


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_MOCK_EXTRACTION_DATA = {
    "repo_rate_pct": 6.5,
    "rbi_stance": "neutral",
    "cpi_yoy_pct": 4.8,
    "nifty50_pe": 22.3,
    "nifty_midcap150_pe": 31.5,
    "nifty_smallcap250_pe": 27.1,
    "gsec_10yr_yield_pct": 7.1,
    "sbi_fd_1yr_rate_pct": 6.8,
    "gold_price_inr_per_10g": 72500.0,
    "gold_price_usd_per_oz": 2350.0,
    "fed_funds_rate_pct": 4.5,
    "fii_net_flows_cr_inr": -5200.0,
    "brent_crude_usd": 78.5,
    "usd_inr_rate": 83.2,
}


def _make_mock_snapshot(data: dict = None, **overrides) -> MacroSnapshot:
    base = dict(_MOCK_EXTRACTION_DATA)
    if data:
        base.update(data)
    base.update(overrides)
    gaps = [k for k, v in base.items() if v is None]
    return MacroSnapshot(**base, data_gaps=gaps)


def _make_mock_scraper_results() -> dict:
    return {
        "repo_rate": "RBI repo rate is 6.5% as of Feb 2026.",
        "rbi_stance": "RBI maintains neutral stance in latest MPC meeting.",
        "cpi_inflation": "India CPI inflation was 4.8% YoY in January 2026.",
        "nifty50_pe": "Nifty 50 PE ratio is 22.3 as of today.",
        "midcap150_pe": "Nifty Midcap 150 PE stands at 31.5.",
        "smallcap250_pe": "Nifty Smallcap 250 PE is currently 27.1.",
        "gsec_10yr_yield": "India 10-year G-sec yield is 7.10%.",
        "sbi_fd_rate": "SBI FD rate for 1 year is 6.80%.",
        "gold_price_inr": "Gold price in India is Rs 72,500 per 10 gram.",
        "gold_price_usd": "Gold is trading at $2,350 per ounce.",
        "fed_rate": "Fed funds rate is at 4.50%.",
        "fii_flows": "FIIs sold Rs 5,200 crore net in Indian equities.",
        "brent_crude": "Brent crude is trading at $78.50 per barrel.",
        "usd_inr": "USD/INR exchange rate is 83.20.",
    }


# ---------------------------------------------------------------------------
# Part 1 — MarketCommentaryAgent tests
# ---------------------------------------------------------------------------


class TestMarketCommentaryAgent(unittest.TestCase):

    def _build_agent(self, tmpdir: str, generate_document: bool = False) -> MarketCommentaryAgent:
        return MarketCommentaryAgent(
            api_key="test-key",
            output_dir=tmpdir,
            generate_document=generate_document,
        )

    # --- 1. Full run success (with document generation) ---

    def test_full_run_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = self._build_agent(tmpdir, generate_document=True)
            agent._scraper.scrape_all = MagicMock(
                return_value=_make_mock_scraper_results()
            )
            mock_snapshot = _make_mock_snapshot()

            with patch("market_commentary.main.extraction_chain") as mock_extraction, \
                 patch("market_commentary.main.document_generation_chain") as mock_doc:
                mock_extraction.invoke.return_value = mock_snapshot
                mock_doc.invoke.return_value = "# Mock Commentary\n\nTest content."

                result = agent.run()

            self.assertIsInstance(result, MacroSnapshot)
            self.assertEqual(result.repo_rate_pct, 6.5)
            self.assertEqual(result.nifty50_pe, 22.3)
            self.assertEqual(result.rbi_stance, "neutral")
            self.assertEqual(result.brent_crude_usd, 78.5)
            self.assertEqual(result.document_md, "# Mock Commentary\n\nTest content.")

    # --- 2. Partial search failure (gaps tracked) ---

    def test_partial_search_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = self._build_agent(tmpdir)

            partial_data = dict(_MOCK_EXTRACTION_DATA)
            partial_data["nifty50_pe"] = None
            partial_data["gold_price_inr_per_10g"] = None
            mock_snapshot = _make_mock_snapshot(partial_data)

            with patch("market_commentary.main.extraction_chain") as mock_extraction:
                mock_extraction.invoke.return_value = mock_snapshot
                result = agent.run()

            self.assertIsNone(result.nifty50_pe)
            self.assertIsNone(result.gold_price_inr_per_10g)
            self.assertIn("nifty50_pe", result.data_gaps)
            self.assertIn("gold_price_inr_per_10g", result.data_gaps)

    # --- 3. JSON file written ---

    def test_json_file_written(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = self._build_agent(tmpdir)
            mock_snapshot = _make_mock_snapshot()

            with patch("market_commentary.main.extraction_chain") as mock_extraction:
                mock_extraction.invoke.return_value = mock_snapshot
                agent.run()

            json_files = [
                f for f in os.listdir(tmpdir)
                if f.startswith("macro_snapshot_") and f.endswith(".json")
            ]
            self.assertEqual(len(json_files), 1)

            with open(os.path.join(tmpdir, json_files[0])) as f:
                data = json.load(f)
            self.assertIn("repo_rate_pct", data)
            self.assertIn("nifty50_pe", data)
            self.assertIn("data_gaps", data)

    # --- 4. Extraction chain error propagates ---

    def test_extraction_error_propagates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = self._build_agent(tmpdir)

            with patch("market_commentary.main.extraction_chain") as mock_extraction:
                mock_extraction.invoke.side_effect = ValueError("LLM extraction failed")

                with self.assertRaises(ValueError):
                    agent.run()

    # --- 5. Cache fallback ---

    def test_cache_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Pre-populate cache
            cached_snapshot = MacroSnapshot(repo_rate_pct=6.5, nifty50_pe=21.0)
            cache_path = os.path.join(tmpdir, "market_commentary_cache.json")
            CacheManager.save(cached_snapshot, cache_path)

            agent = self._build_agent(tmpdir)

            # extraction_chain returns all-null snapshot → triggers cache fallback
            all_null_snapshot = MacroSnapshot(data_gaps=[])

            with patch("market_commentary.main.extraction_chain") as mock_extraction:
                mock_extraction.invoke.return_value = all_null_snapshot
                result = agent.run()

            self.assertEqual(result.repo_rate_pct, 6.5)
            self.assertEqual(result.nifty50_pe, 21.0)
            self.assertTrue(
                any("cache" in gap.lower() for gap in result.data_gaps)
            )

    # --- 6. Scraper never raises ---

    def test_scraper_never_raises(self):
        scraper = IndicatorScraper()  # TavilyClient is MagicMock
        result = scraper._search_one("this will fail")
        self.assertIsInstance(result, str)


# ---------------------------------------------------------------------------
# Part 2 — Chat Q&A tests
# ---------------------------------------------------------------------------


class TestChatQA(unittest.TestCase):

    _SAMPLE_COMMENTARY = """\
# Prozper Asset Management — Market Commentary | April 2026

## Executive Summary
- RBI repo rate held at 6.5% with neutral stance.
- Nifty 50 PE at 22.3x — fair value range.
- Gold at ₹72,500 per 10g driven by global demand.
"""

    # --- 7. answer_question uses pre-loaded document ---

    def test_answer_question_with_provided_document(self):
        with patch("market_commentary.chat_qa.qa_chain") as mock_chain:
            mock_chain.invoke.return_value = "The RBI repo rate is 6.5%."

            result = answer_question(
                user_question="What is the current repo rate?",
                document_content=self._SAMPLE_COMMENTARY,
            )

        self.assertEqual(result, "The RBI repo rate is 6.5%.")
        mock_chain.invoke.assert_called_once_with({
            "document_content": self._SAMPLE_COMMENTARY,
            "user_question": "What is the current repo rate?",
        })

    # --- 8. load_latest_commentary reads newest file ---

    def test_load_latest_commentary_reads_newest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            older = os.path.join(tmpdir, "macro_snapshot_20260401_120000.md")
            newer = os.path.join(tmpdir, "macro_snapshot_20260405_090000.md")
            with open(older, "w") as f:
                f.write("Old commentary")
            with open(newer, "w") as f:
                f.write("New commentary")

            result = load_latest_commentary(tmpdir)

        self.assertEqual(result, "New commentary")

    # --- 9. load_latest_commentary raises when no files exist ---

    def test_load_latest_commentary_raises_when_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(FileNotFoundError):
                load_latest_commentary(tmpdir)

    # --- 10. answer_question loads from disk when no document_content ---

    def test_answer_question_loads_from_disk(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = os.path.join(tmpdir, "macro_snapshot_20260405_090000.md")
            with open(md_path, "w") as f:
                f.write(self._SAMPLE_COMMENTARY)

            with patch("market_commentary.chat_qa.qa_chain") as mock_chain:
                mock_chain.invoke.return_value = "Nifty 50 PE is 22.3x."
                result = answer_question(
                    user_question="What is the Nifty PE?",
                    output_dir=tmpdir,
                )

        self.assertEqual(result, "Nifty 50 PE is 22.3x.")
        call_args = mock_chain.invoke.call_args[0][0]
        self.assertIn("22.3x", call_args["document_content"])
        self.assertEqual(call_args["user_question"], "What is the Nifty PE?")


if __name__ == "__main__":
    unittest.main()
