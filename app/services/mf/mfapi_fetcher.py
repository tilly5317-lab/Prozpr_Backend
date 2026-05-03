"""Async client for the public mfapi.in mutual-fund data feed.

Two operations:
- ``fetch_universe`` — `GET /mf` returns every scheme (``schemeCode``, ``schemeName``,
  optional ``isinGrowth``, ``isinDivReinvestment`` when present).
- ``fetch_scheme_detail`` — `GET /mf/{scheme_code}` returns scheme meta (with ISINs)
  plus a full ``data[]`` of NAV history (newest-first).

Patterns lifted from ``MF_Logics/Mututal_Funds_data_extraction/mf_pipeline_common.py``
(retry-with-backoff, bounded concurrency); this is the active async path.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

import httpx

from app.models.mf.enums import MfOptionType, MfPlanType

logger = logging.getLogger(__name__)


MFAPI_BASE = "https://api.mfapi.in"
MFAPI_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
MFAPI_MAX_RETRIES = 3
MFAPI_CONCURRENCY = 12


class MfapiFetchError(RuntimeError):
    """Raised when the mfapi.in feed cannot be retrieved or parsed at all."""


@dataclass(slots=True)
class UniverseRow:
    scheme_code: str
    scheme_name: str
    isin_growth: Optional[str] = None
    isin_div_reinvest: Optional[str] = None


@dataclass(slots=True)
class NavPoint:
    nav_date: date
    nav: Decimal


@dataclass(slots=True)
class SchemeDetail:
    scheme_code: str
    scheme_name: str
    fund_house: str
    scheme_type: str
    scheme_category: str
    isin_growth: Optional[str]
    isin_div_reinvest: Optional[str]
    plan_type: MfPlanType
    option_type: MfOptionType
    navs: list[NavPoint] = field(default_factory=list)
    parse_errors: int = 0


_DIRECT_RE = re.compile(r"\b(direct\s*plan|-\s*direct\b)", re.IGNORECASE)
_GROWTH_RE = re.compile(r"\b(growth)\b", re.IGNORECASE)
_IDCW_RE = re.compile(r"\b(idcw|dividend|payout|reinvest)\b", re.IGNORECASE)


def _derive_plan_type(scheme_name: str) -> MfPlanType:
    return MfPlanType.DIRECT if _DIRECT_RE.search(scheme_name or "") else MfPlanType.REGULAR


def _derive_option_type(scheme_name: str) -> MfOptionType:
    name = scheme_name or ""
    if _GROWTH_RE.search(name):
        return MfOptionType.GROWTH
    if _IDCW_RE.search(name):
        return MfOptionType.IDCW
    return MfOptionType.GROWTH


def _coerce_isin(value: object) -> Optional[str]:
    if not value:
        return None
    s = str(value).strip().upper()
    if not s or s in {"-", "N.A.", "NA", "NONE"}:
        return None
    if len(s) != 12 or not s.isalnum():
        return None
    return s


async def _request_json(client: httpx.AsyncClient, url: str) -> object:
    last_exc: Optional[Exception] = None
    for attempt in range(1, MFAPI_MAX_RETRIES + 1):
        try:
            resp = await client.get(url)
            if resp.status_code >= 500 or resp.status_code == 429:
                raise httpx.HTTPStatusError(
                    f"transient {resp.status_code}", request=resp.request, response=resp
                )
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            last_exc = exc
            if attempt == MFAPI_MAX_RETRIES:
                break
            await asyncio.sleep(2 ** (attempt - 1))
    raise MfapiFetchError(f"mfapi.in request failed for {url}: {last_exc}") from last_exc


async def fetch_universe(client: httpx.AsyncClient) -> list[UniverseRow]:
    payload = await _request_json(client, f"{MFAPI_BASE}/mf")
    if not isinstance(payload, list) or not payload:
        raise MfapiFetchError("mfapi.in /mf returned empty or non-list payload")
    rows: list[UniverseRow] = []
    for entry in payload:
        try:
            code = str(entry["schemeCode"]).strip()
            name = str(entry.get("schemeName") or "").strip()
            isin_g = _coerce_isin(entry.get("isinGrowth"))
            isin_dr = _coerce_isin(entry.get("isinDivReinvestment"))
        except (KeyError, TypeError):
            continue
        if code:
            rows.append(
                UniverseRow(
                    scheme_code=code,
                    scheme_name=name,
                    isin_growth=isin_g,
                    isin_div_reinvest=isin_dr,
                )
            )
    if not rows:
        raise MfapiFetchError("mfapi.in /mf parsed to zero schemes")
    return rows


def _parse_navs(raw: object) -> tuple[list[NavPoint], int]:
    points: list[NavPoint] = []
    errors = 0
    if not isinstance(raw, list):
        return points, errors
    for entry in raw:
        try:
            d = datetime.strptime(str(entry["date"]).strip(), "%d-%m-%Y").date()
            n = Decimal(str(entry["nav"]).strip())
        except (KeyError, ValueError, InvalidOperation, TypeError):
            errors += 1
            continue
        points.append(NavPoint(nav_date=d, nav=n))
    return points, errors


async def fetch_scheme_detail(
    client: httpx.AsyncClient, scheme_code: str
) -> Optional[SchemeDetail]:
    payload = await _request_json(client, f"{MFAPI_BASE}/mf/{scheme_code}")
    if not isinstance(payload, dict):
        raise MfapiFetchError(f"mfapi.in /mf/{scheme_code} returned non-object")
    status = str(payload.get("status") or "").upper()
    if status and status != "SUCCESS":
        raise MfapiFetchError(f"mfapi.in /mf/{scheme_code} status={status}")
    meta = payload.get("meta") or {}
    if not isinstance(meta, dict):
        raise MfapiFetchError(f"mfapi.in /mf/{scheme_code} missing meta")

    code = str(meta.get("scheme_code") or scheme_code).strip()
    scheme_name = str(meta.get("scheme_name") or "").strip()
    if not code or not scheme_name:
        raise MfapiFetchError(f"mfapi.in /mf/{scheme_code} missing code/name")

    navs, errors = _parse_navs(payload.get("data"))

    return SchemeDetail(
        scheme_code=code,
        scheme_name=scheme_name,
        fund_house=str(meta.get("fund_house") or "").strip() or "Unknown",
        scheme_type=str(meta.get("scheme_type") or "").strip(),
        scheme_category=str(meta.get("scheme_category") or "").strip(),
        isin_growth=_coerce_isin(meta.get("isin_growth")),
        isin_div_reinvest=_coerce_isin(meta.get("isin_div_reinvestment")),
        plan_type=_derive_plan_type(scheme_name),
        option_type=_derive_option_type(scheme_name),
        navs=navs,
        parse_errors=errors,
    )


async def fetch_many_scheme_details(
    client: httpx.AsyncClient,
    scheme_codes: list[str],
    *,
    concurrency: int = MFAPI_CONCURRENCY,
) -> tuple[list[SchemeDetail], list[str]]:
    """Fetch many scheme details concurrently. Returns (details, failed_codes)."""

    sem = asyncio.Semaphore(concurrency)
    details: list[SchemeDetail] = []
    failed: list[str] = []

    async def _one(code: str) -> None:
        async with sem:
            try:
                detail = await fetch_scheme_detail(client, code)
            except MfapiFetchError as exc:
                logger.warning("mfapi fetch failed for %s: %s", code, exc)
                failed.append(code)
                return
            except Exception as exc:
                logger.exception("mfapi unexpected error for %s: %s", code, exc)
                failed.append(code)
                return
            if detail is not None:
                details.append(detail)

    await asyncio.gather(*(_one(c) for c in scheme_codes))
    return details, failed
