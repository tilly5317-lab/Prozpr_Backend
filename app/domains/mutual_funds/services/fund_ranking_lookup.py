"""Thin lookups over the cached fund-ranking CSV.

The loader (`rebal_engine.fund_rank`) exposes the shortlist grouped by
`asset_subgroup`; mutual_fund_query needs by-ISIN access and like-for-like peers by
`sub_category`, so those wrappers live here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.domains.rebalancing.services.rebal_engine.fund_rank import (
    FundRankRow,
    get_fund_ranking,
    get_rejection_reasons,
)
from app.domains.rebalancing.services.rebal_engine import fund_rank as _fund_rank


def _all_rows() -> list[FundRankRow]:
    return [r for rows in get_fund_ranking().values() for r in rows]


def ranking_by_isin(isin: str) -> FundRankRow | None:
    """The shortlist row for a fund by ISIN, or ``None`` if not in the shortlist."""
    return next((r for r in _all_rows() if r.isin == isin), None)


def peers_by_sub_category(sub_category: str, exclude_isin: str) -> list[FundRankRow]:
    """Shortlist funds of the same `sub_category` (like-for-like), sorted by rank,
    excluding the fund itself."""
    peers = [
        r for r in _all_rows()
        if r.sub_category == sub_category and r.isin != exclude_isin
    ]
    return sorted(peers, key=lambda r: r.rank)


# ── Customer-named-fund resolution against the ranking CSV ────────────────────
# Distinct from fund_resolver_service.resolve_fund (DB-backed identity over the
# full scheme universe): this matches ONLY the ranking CSV, because rejected
# rows — and their rejection reasons — exist only there. Deliberately
# conservative: normalized substring, >1 distinct ISIN = ambiguous, never guess.

_STOPWORDS = {"fund", "plan", "direct", "growth", "the", "of"}

# Plan/option words that distinguish variants of the SAME fund, not different
# funds — dropped when computing a fund's identity so "X Direct Growth" and
# "X Regular IDCW" collapse to one fund (mirrors fund_resolver_service).
_PLAN_OPTION = {
    "direct", "regular", "growth", "idcw", "plan", "option",
    "dividend", "payout", "reinvestment",
}


def _norm(s: str) -> str:
    return " ".join(s.lower().replace("-", " ").split())


def _fund_key(name: str) -> frozenset[str]:
    """A scheme name reduced to its fund identity (plan/option words dropped)."""
    import re

    return frozenset(re.findall(r"[a-z0-9]+", (name or "").lower())) - _PLAN_OPTION


def _pick_variant(rows: list[FundRankRow]) -> FundRankRow:
    """Canonical representative of one fund: prefer Direct-Growth, then Growth."""
    dg = [r for r in rows if {"direct", "growth"} <= set(_norm(r.fund_name).split())]
    if dg:
        return dg[0]
    g = [r for r in rows if "growth" in _norm(r.fund_name).split()]
    return (g or rows)[0]


@dataclass(frozen=True)
class FundResolution:
    status: Literal["recommended", "rejected", "ambiguous", "unknown"]
    isin: str | None = None
    fund_name: str | None = None
    sub_category: str | None = None
    rejection_text: str | None = None
    candidates: tuple[str, ...] = ()


def resolve_ranked_fund(text: str) -> FundResolution:
    needle = _norm(text)
    if not needle or all(t in _STOPWORDS for t in needle.split()):
        return FundResolution(status="unknown")
    hits = [r for r in _fund_rank.get_all_rows() if needle in _norm(r.fund_name)]
    if not hits:
        return FundResolution(status="unknown")

    # Group by fund identity so plan/option variants of one fund collapse;
    # only genuinely different funds make the ask ambiguous.
    families: dict[frozenset[str], list[FundRankRow]] = {}
    for r in hits:
        families.setdefault(_fund_key(r.fund_name), []).append(r)
    if len(families) > 1:
        names = tuple(sorted(_pick_variant(g).fund_name for g in families.values())[:5])
        return FundResolution(status="ambiguous", candidates=names)

    fam = next(iter(families.values()))
    rejections = get_rejection_reasons()
    # A recommended variant (rank ≥ 1) wins over rejected ones in the same family.
    rec = next((r for r in fam if r.rank and r.rank >= 1), None)
    if rec is not None:
        return FundResolution(status="recommended", isin=rec.isin,
                              fund_name=rec.fund_name, sub_category=rec.sub_category)
    row = _pick_variant(fam)
    reason = next((rejections[r.isin] for r in fam if rejections.get(r.isin)), None)
    if reason:
        return FundResolution(status="rejected", isin=row.isin,
                              fund_name=row.fund_name, sub_category=row.sub_category,
                              rejection_text=reason)
    return FundResolution(status="recommended", isin=row.isin,
                          fund_name=row.fund_name, sub_category=row.sub_category)
