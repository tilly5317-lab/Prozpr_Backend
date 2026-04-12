from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Dict

from tavily import TavilyClient

# ---------------------------------------------------------------------------
# One search query per macro indicator.
# Queries are crafted to surface a clear numeric value in the top results.
# ---------------------------------------------------------------------------

_SEARCH_QUERIES: Dict[str, str] = {
    "repo_rate": "RBI repo rate India current",
    "rbi_stance": "RBI monetary policy stance current accommodative neutral hawkish",
    "cpi_inflation": "India CPI inflation latest month YoY percentage",
    "nifty50_pe": "Nifty 50 PE ratio today",
    "midcap150_pe": "Nifty Midcap 150 PE ratio today",
    "smallcap250_pe": "Nifty Smallcap 250 PE ratio today",
    "gsec_10yr_yield": "India 10 year government bond yield today",
    "sbi_fd_rate": "SBI fixed deposit interest rate 1 year today",
    "gold_price_inr": "gold price India today per 10 gram rupees",
    "gold_price_usd": "gold price today USD per troy ounce",
    "fed_rate": "US federal funds rate current percentage",
    "fii_flows": "FII FPI net flows India latest month crore",
    "brent_crude": "Brent crude oil price today USD per barrel",
    "usd_inr": "USD INR exchange rate today",
}


class IndicatorScraper:
    """Searches the web for all macro indicators via Tavily.

    Each indicator gets its own search query. Queries run concurrently
    via a thread pool. Individual search failures are silently caught
    and return an empty string — the pipeline can still proceed with
    partial data.
    """

    def __init__(self, max_workers: int = 7) -> None:
        self._client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY", ""))
        self._max_workers = max_workers

    def scrape_all(self) -> Dict[str, str]:
        """Return ``{indicator_name: raw_search_snippets}`` for all indicators.

        Runs all searches concurrently. Never raises — individual failures
        produce empty strings.
        """
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures = {
                name: pool.submit(self._search_one, query)
                for name, query in _SEARCH_QUERIES.items()
            }
            return {name: fut.result() for name, fut in futures.items()}

    def _search_one(self, query: str) -> str:
        """Run one Tavily search and return concatenated content snippets.

        Returns an empty string on any error (network failure, API limit, etc.).
        """
        try:
            response = self._client.search(query, max_results=5)
            results = response.get("results", [])
            return "\n".join(r.get("content", "") for r in results)
        except Exception:
            return ""
