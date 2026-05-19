"""Tier 3 scraper using Groww's public JSON APIs (no Cloudflare wall).

Two-step flow per fund:
  1. Search Groww by cleaned scheme name -> get search_id slug
  2. GET v2 scheme detail by search_id -> extract AUM, expense, holdings, manager, etc.

Cached by AMFI scheme_code under build/cache/scrape/<code>.json.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

CACHE_DIR = Path(__file__).resolve().parents[1] / "build" / "cache" / "scrape"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json",
}

SEARCH_URL = "https://groww.in/v1/api/search/v3/query/global/st_query"
DETAIL_URL = "https://groww.in/v1/api/data/mf/web/v2/scheme/search/{slug}"

PLAN_NOISE = re.compile(
    r"\s*(?:-\s*)?(direct|regular)\s*(plan|option)?\b|"
    r"\s*-?\s*(growth|idcw|dividend|payout|reinvest(?:ment)?|bonus)(?:\s+option)?|"
    r"\s*-\s*growth\s*option|\s*-\s*direct\s*plan",
    re.IGNORECASE,
)


def clean_query(scheme_name: str) -> str:
    s = scheme_name or ""
    s = PLAN_NOISE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip(" -")
    return s


def _get(url: str, params: dict | None = None, timeout: int = 12) -> dict | None:
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
                return r.json()
            if r.status_code in (404, 400):
                return None
        except Exception as e:
            print(f"  groww err attempt {attempt}: {e}")
        time.sleep(0.7 * (attempt + 1))
    return None


def search_slug(scheme_name: str) -> str | None:
    q = clean_query(scheme_name)
    if not q:
        return None
    j = _get(SEARCH_URL, params={"from": 0, "size": 5, "query": q})
    if not j:
        return None
    content = j.get("data", {}).get("content", []) or []
    if not content:
        return None
    # Prefer titles whose lowered words overlap most with the query
    qwords = set(re.findall(r"\w+", q.lower()))
    best = None
    best_score = -1
    for c in content:
        title = (c.get("title") or "").lower()
        twords = set(re.findall(r"\w+", title))
        score = len(qwords & twords) - 0.1 * abs(len(twords) - len(qwords))
        if score > best_score:
            best_score = score
            best = c
    if best and best.get("search_id"):
        return best["search_id"]
    return content[0].get("search_id")


def fetch_detail_by_slug(slug: str) -> dict | None:
    return _get(DETAIL_URL.format(slug=slug))


def fetch_for_scheme(scheme_code: int, scheme_name: str, force: bool = False) -> dict | None:
    cache_file = CACHE_DIR / f"{scheme_code}.json"
    if cache_file.exists() and not force:
        with open(cache_file) as f:
            return json.load(f)
    slug = search_slug(scheme_name)
    if not slug:
        cache_file.write_text(json.dumps({"_status": "no_search_match", "scheme_name": scheme_name}))
        return None
    detail = fetch_detail_by_slug(slug)
    if not detail:
        cache_file.write_text(json.dumps({"_status": "no_detail", "slug": slug}))
        return None
    detail["_slug"] = slug
    with open(cache_file, "w") as f:
        json.dump(detail, f)
    time.sleep(0.4)  # be polite
    return detail


def _safe_isin_match(detail: dict, target_isins: tuple[str, ...]) -> bool:
    """True if Groww's ISIN matches any of the AMFI-side ISINs we hold for this scheme.

    A scheme can have two ISINs (Growth + Dividend-Reinvest) sharing one NAV; Groww
    returns one of them, so equality against any is the right check.
    """
    targets = {t.strip().upper() for t in target_isins if t}
    if not targets:
        return True  # nothing to check against
    d_isin = (detail.get("isin") or "").strip().upper()
    return d_isin in targets


def extract_tier3(detail: dict | None, target_isins: tuple[str, ...] = ()) -> dict:
    """Normalize Groww detail into the framework's Tier 3 fields."""
    out = {
        "asset_size_cr": None,           # AUM in INR crore
        "min_investment": None,
        "expense_ratio_pct": None,
        "performance_fees": "Not applicable in Indian MFs",
        "entry_load": "Nil (regulatory)",
        "exit_load": None,
        "exit_load_period": None,
        "lead_pm_name": None,
        "manager_start_date": None,
        "pm_turnover_l5y": None,
        "top10_holdings_weight_pct": None,
        "size_exposure_LMS": None,
        "style_exposure_GV": None,        # not in Groww; leave null
        "portfolio_churn_l3y_pct": None,
        "groww_benchmark": None,
        "groww_isin_match": None,
        "_groww_slug": None,
    }
    if not detail or detail.get("_status"):
        return out
    out["_groww_slug"] = detail.get("_slug")
    out["groww_isin_match"] = _safe_isin_match(detail, target_isins)
    out["asset_size_cr"] = detail.get("aum")
    out["min_investment"] = detail.get("min_investment_amount")
    out["expense_ratio_pct"] = detail.get("expense_ratio")
    out["exit_load"] = detail.get("exit_load")
    out["portfolio_churn_l3y_pct"] = detail.get("portfolio_turnover")  # latest, proxy
    out["groww_benchmark"] = detail.get("benchmark_name") or detail.get("benchmark")

    # Exit load period: parse text like "Exit load of 1% if redeemed within 1 year"
    el = detail.get("exit_load") or ""
    m = re.search(r"within\s+(\d+\s*(?:year|month|day)s?)", el, re.I)
    if m:
        out["exit_load_period"] = m.group(1)

    # Fund manager: pick the one with the latest date_from among current managers
    fmd = detail.get("fund_manager_details") or []
    current = [m for m in fmd if m.get("plan_id") and not m.get("date_to")]
    pool = current or fmd
    if pool:
        # Earliest date_from = lead manager's tenure start; pick the one named in fund_manager
        pm_name_hint = (detail.get("fund_manager") or "").split(",")[0].strip()
        match = next((m for m in pool if pm_name_hint and pm_name_hint.lower() in (m.get("person_name") or "").lower()), None) or pool[0]
        out["lead_pm_name"] = match.get("person_name")
        df = match.get("date_from")
        if df:
            try:
                out["manager_start_date"] = datetime.fromisoformat(df.replace("Z", "+00:00")).strftime("%Y-%m-%d")
            except Exception:
                out["manager_start_date"] = df

    # PM turnover L5Y: count distinct managers across plans in last 5 years
    if fmd:
        cutoff = datetime.now().replace(tzinfo=None)
        cutoff = cutoff.replace(year=cutoff.year - 5)
        seen = set()
        for m in fmd:
            df = m.get("date_from")
            if df:
                try:
                    d = datetime.fromisoformat(df.replace("Z", "+00:00")).replace(tzinfo=None)
                    if d >= cutoff:
                        seen.add(m.get("person_name"))
                except Exception:
                    pass
        out["pm_turnover_l5y"] = len(seen) if seen else None

    # Holdings — top 10 weight (size exposure not in this endpoint, leave null)
    holdings = detail.get("holdings") or []
    equity_h = [h for h in holdings if (h.get("nature_name") or "").upper() in {"EQ", "EQUITY", "STOCK", "EQUITIES"}]
    if equity_h:
        sorted_h = sorted(equity_h, key=lambda x: x.get("corpus_per", 0) or 0, reverse=True)
        top10 = sorted_h[:10]
        out["top10_holdings_weight_pct"] = round(sum((h.get("corpus_per", 0) or 0) for h in top10), 2)

    # Category rank (current) — useful even though not strictly the framework's "rolling %ile"
    stats = detail.get("stats") or []
    rank_row = next((s for s in stats if s.get("type") == "RANK_WITHIN_CATEGORY"), None)
    cat_avg_row = next((s for s in stats if s.get("type") == "CATEGORY_AVG_RETURN"), None)
    if rank_row:
        out["category_rank_1y"] = rank_row.get("stat_1y")
        out["category_rank_3y"] = rank_row.get("stat_3y")
        out["category_rank_5y"] = rank_row.get("stat_5y")
    if cat_avg_row:
        out["category_avg_3y_pct"] = cat_avg_row.get("stat_3y")
    return out


if __name__ == "__main__":
    pilots = [
        (119018, "HDFC Large Cap Fund - Direct Growth", "INF179K01YV8"),
        (122639, "Parag Parikh Flexi Cap Fund - Direct Plan - Growth", ""),
        (119091, "HDFC Liquid Fund - Direct Growth", ""),
    ]
    for code, name, isin in pilots:
        print(f"\n=== {code} {name[:60]} ===")
        d = fetch_for_scheme(code, name)
        t3 = extract_tier3(d, (isin,))
        for k, v in t3.items():
            print(f"  {k}: {v}")
