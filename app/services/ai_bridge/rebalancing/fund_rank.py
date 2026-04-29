# app/services/ai_bridge/rebalancing/fund_rank.py
"""Loader for the static fund-rank CSV consumed by the rebalancing input builder.

The CSV is a 1:N mapping from ``asset_subgroup`` to ranked recommended funds. It
is loaded once at module import time and cached as a frozen dict; no DB calls.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from functools import cache
from pathlib import Path


_CSV_PATH = Path(__file__).resolve().parents[4] / "AI_Agents" / "Reference_docs" / "Prozpr_fund_ranking.csv"


@dataclass(frozen=True)
class FundRankRow:
    asset_subgroup: str
    sub_category: str
    rank: int
    isin: str
    fund_name: str


@cache
def get_fund_ranking() -> dict[str, list[FundRankRow]]:
    """Return ``{asset_subgroup: [FundRankRow, ...]}`` sorted by rank.

    Cached for the lifetime of the process. To force a reload (e.g. after
    swapping the CSV in tests), call ``get_fund_ranking.cache_clear()``.
    """
    by_sg: dict[str, list[FundRankRow]] = defaultdict(list)
    with open(_CSV_PATH, newline="") as f:
        for row in csv.DictReader(f):
            by_sg[row["asset_subgroup"]].append(FundRankRow(
                asset_subgroup=row["asset_subgroup"],
                sub_category=row["sub_category"],
                rank=int(row["rank"]),
                isin=row["isin"],
                fund_name=row["recommended_fund"],
            ))
    for subgroup in by_sg:
        by_sg[subgroup].sort(key=lambda r: r.rank)
    return dict(by_sg)
