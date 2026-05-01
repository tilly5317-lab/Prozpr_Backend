from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Dict

from ddgs import DDGS

# ---------------------------------------------------------------------------
# One search query per macro indicator.
# Queries are crafted to surface a clear numeric value in the top results.
# ---------------------------------------------------------------------------

_SEARCH_QUERIES: Dict[str, str] = {
    "repo_rate": "RBI repo rate India current",
    "rbi_stance": "RBI monetary policy stance current accommodative neutral hawkish",
    "cpi_inflation": '"India CPI inflation" 2026 YoY percent latest',
    "nifty50_pe": '"Nifty 50" PE ratio trendlyne 2026',
    "midcap150_pe": "Nifty Midcap 150 PE ratio today",
    "smallcap250_pe": '"Nifty Smallcap 250" PE ratio index valuation 2026',
    "gsec_10yr_yield": "India 10 year G-Sec yield 2026 percent worldgovernmentbonds",
    "sbi_fd_rate": 'SBI "1 year" fixed deposit interest rate 2026 percent',
    "gold_price_inr": "24 carat gold price India 10 grams rupees today 2026",
    "gold_price_usd": "gold spot price USD per troy ounce today 2026",
    "fed_rate": "US federal funds rate current percentage",
    "fii_flows": "FII FPI net flows India 2026 crore NSE moneycontrol",
    "brent_crude": "Brent crude oil price today USD per barrel",
    "usd_inr": "USD INR exchange rate today",
}


class IndicatorScraper:
    """Searches the web for all macro indicators via DuckDuckGo (keyless).

    Each indicator gets its own search query. Queries run concurrently
    via a thread pool. Individual search failures are silently caught
    and return an empty string — the pipeline can still proceed with
    partial data.
    """

    def __init__(self, max_workers: int = 3) -> None:
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
        """Run one DDG search and return concatenated snippet bodies.

        Returns an empty string on any error (network failure, rate limit, etc.).
        """
        try:
            with DDGS() as client:
                results = list(client.text(query, max_results=10))
            lines = []
            for r in results:
                title = (r.get("title") or "").strip()
                body = (r.get("body") or "").strip()
                if title and body:
                    lines.append(f"{title} — {body}")
                else:
                    lines.append(title or body)
            return "\n".join(line for line in lines if line)
        except Exception:
            return ""
