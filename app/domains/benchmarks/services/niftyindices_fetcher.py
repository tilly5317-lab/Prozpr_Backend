"""Async client (scraper) for the niftyindices.com Total Return Index (TRI) feed.

One operation: POST ``getTotalReturnIndexString`` returns daily TRI rows for an
index over a date range. Long ranges time out, so callers use ``fetch_tri_chunked``
to split into <=2-year windows. Mirrors the retry-with-backoff posture of
``mutual_funds.services.mfapi_fetcher``.

This is the reusable scraper used both by the daily ``benchmark_scheduler`` and
the one-time ``scripts.backfill_nifty50`` temp scraper. Source page:
https://www.niftyindices.com/reports/historical-data
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

NIFTY_TRI_URL = "https://www.niftyindices.com/Backpage.aspx/getTotalReturnIndexString"
NIFTY_TRI_HEADERS = {
    "Content-Type": "application/json; charset=UTF-8",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://www.niftyindices.com/reports/historical-data",
    "X-Requested-With": "XMLHttpRequest",
}
NIFTY_TRI_TIMEOUT = httpx.Timeout(connect=10.0, read=90.0, write=10.0, pool=10.0)
NIFTY_TRI_MAX_RETRIES = 3
DEFAULT_INDEX_NAME = "NIFTY 50"
NIFTY_TRI_EARLIEST = date(1999, 6, 30)  # earliest TRI date published by NSE


class NiftyTriFetchError(RuntimeError):
    """Raised when the niftyindices TRI feed cannot be retrieved or parsed."""


@dataclass(slots=True)
class TriRow:
    tri_date: date
    tri_value: Decimal
    ntr_value: Optional[Decimal]


def _fmt(d: date) -> str:
    """niftyindices expects 'DD-Mon-YYYY' (e.g. 04-Jun-2026)."""
    return d.strftime("%d-%b-%Y")


def _parse_record(rec: dict) -> Optional[TriRow]:
    """Map one response record -> TriRow; return None if malformed."""
    try:
        d = datetime.strptime(str(rec["Date"]).strip(), "%d %b %Y").date()
        tri = Decimal(str(rec["TotalReturnsIndex"]).strip())
    except (KeyError, ValueError, InvalidOperation, TypeError):
        return None
    ntr_raw = rec.get("NTR_Value")
    try:
        ntr = Decimal(str(ntr_raw).strip()) if ntr_raw not in (None, "") else None
    except (InvalidOperation, ValueError, TypeError):
        ntr = None
    return TriRow(tri_date=d, tri_value=tri, ntr_value=ntr)


def iter_date_windows(start: date, end: date, window_years: int = 2):
    """Yield contiguous, non-overlapping (start, end) windows of <= window_years."""
    cur = start
    while cur <= end:
        win_end = min(
            date(cur.year + window_years, cur.month, cur.day) - timedelta(days=1), end
        )
        yield cur, win_end
        cur = win_end + timedelta(days=1)


async def fetch_tri(
    client: httpx.AsyncClient,
    index_name: str,
    start: date,
    end: date,
    *,
    max_retries: int = NIFTY_TRI_MAX_RETRIES,
    backoff_base: float = 1.0,
) -> list[TriRow]:
    """Fetch TRI rows for [start, end], parsed and sorted ascending by date."""
    cinfo = "{'name':'%s','startDate':'%s','endDate':'%s','indexName':'%s'}" % (
        index_name,
        _fmt(start),
        _fmt(end),
        index_name,
    )
    body = {"cinfo": cinfo}
    last_exc: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = await client.post(
                NIFTY_TRI_URL,
                json=body,
                headers=NIFTY_TRI_HEADERS,
                timeout=NIFTY_TRI_TIMEOUT,
            )
            if resp.status_code >= 500 or resp.status_code == 429:
                raise httpx.HTTPStatusError(
                    f"transient {resp.status_code}", request=resp.request, response=resp
                )
            resp.raise_for_status()
            outer = resp.json()
            raw = json.loads(outer["d"])  # 'd' is a JSON-encoded string
            rows = [r for r in (_parse_record(rec) for rec in raw) if r is not None]
            rows.sort(key=lambda r: r.tri_date)
            return rows
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            last_exc = exc
            if attempt == max_retries:
                break
            await asyncio.sleep(backoff_base * (2 ** (attempt - 1)))
    raise NiftyTriFetchError(
        f"niftyindices TRI fetch failed for {index_name} {start}..{end}: {last_exc}"
    ) from last_exc


async def fetch_tri_chunked(
    client: httpx.AsyncClient,
    index_name: str,
    start: date,
    end: date,
    *,
    window_years: int = 2,
) -> list[TriRow]:
    """Fetch [start, end] in <=window_years windows; concat + dedupe by date, ascending."""
    by_date: dict[date, TriRow] = {}
    for win_start, win_end in iter_date_windows(start, end, window_years=window_years):
        for row in await fetch_tri(client, index_name, win_start, win_end):
            by_date[row.tri_date] = row
    return [by_date[d] for d in sorted(by_date)]
