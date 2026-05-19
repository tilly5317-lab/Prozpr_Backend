# app/services/ai_bridge/rebalancing/fund_rank.py
"""Loader for the static fund-rank CSV consumed by the rebalancing input builder.

The CSV has two row types:

* Rank populated (1, 2, …) → a recommended fund. `selection_reason` carries the
  positive "why we picked this" string.
* Rank blank → a fund the data team evaluated but rejected. One or more of the
  9 ``*_reason`` columns explain why; ``get_rejection_reasons`` joins them.

Both views are loaded once at module import time and cached as frozen dicts;
no DB calls.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from functools import cache
from pathlib import Path


_CSV_PATH = Path(__file__).resolve().parents[4] / "AI_Agents" / "Reference_docs" / "prozpr_fund_ranking_may_2026.csv"


# Reason columns populated on rank-blank rows. Order matters — text is joined
# in this order so the rendered explanation reads consistently.
_REJECTION_COLUMNS = (
    "custom_reason",
    "pm_tenure_reason",
    "returns_pctile_reason",
    "consistency_reason",
    "direct_regular_reason",
    "div_growth_reason",
    "worst_perf_reason",
    "size_reason",
    "excluded_subgroup_reason",
)


# Used when a held fund is not in the ranking CSV at all (neither recommended
# nor evaluated-and-rejected). The data team's pipeline didn't even consider
# it — so we give the customer a generic but truthful reason.
NOT_EVALUATED_REASON = (
    "This fund didn't make it through our filtering criteria — we recommend exiting it."
)


@dataclass(frozen=True)
class FundRankRow:
    asset_subgroup: str
    sub_category: str
    rank: int
    isin: str
    fund_name: str
    selection_reason: str = ""


@cache
def get_fund_ranking() -> dict[str, list[FundRankRow]]:
    """Return ``{asset_subgroup: [FundRankRow, ...]}`` for recommended funds
    (rank ≥ 1), sorted by rank.

    Cached for the lifetime of the process. To force a reload (e.g. after
    swapping the CSV in tests), call ``get_fund_ranking.cache_clear()``.
    """
    by_sg: dict[str, list[FundRankRow]] = defaultdict(list)
    with open(_CSV_PATH, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rank_raw = (row.get("rank") or "").strip()
            if not rank_raw:
                continue
            by_sg[row["asset_subgroup"]].append(FundRankRow(
                asset_subgroup=row["asset_subgroup"],
                sub_category=row["sub_category"],
                rank=int(rank_raw),
                isin=row["isin"],
                fund_name=row["recommended_fund"],
                selection_reason=(row.get("selection_reason") or "").strip(),
            ))
    for subgroup in by_sg:
        by_sg[subgroup].sort(key=lambda r: r.rank)
    return dict(by_sg)


@cache
def get_rejection_reasons() -> dict[str, str]:
    """Return ``{isin: joined_rejection_text}`` for rank-blank rows in the CSV.

    Non-empty rejection columns are joined with a single space in the order
    declared by ``_REJECTION_COLUMNS``. ISINs whose row has every rejection
    column blank are omitted.
    """
    out: dict[str, str] = {}
    with open(_CSV_PATH, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rank_raw = (row.get("rank") or "").strip()
            if rank_raw:
                continue
            parts: list[str] = []
            for col in _REJECTION_COLUMNS:
                val = (row.get(col) or "").strip()
                if val:
                    parts.append(val)
            if parts:
                out[row["isin"]] = " ".join(parts)
    return out
