# app/services/ai_bridge/rebalancing/fund_rank.py
"""Loader for the static fund-rank CSV consumed by the rebalancing input builder.

The CSV is a 1:N mapping from ``asset_subgroup`` to ranked recommended funds.
If the file is absent (typical in fresh clones), :func:`get_fund_ranking` returns
an empty mapping; the input builder then derives rank-1 targets from held funds
per allocation subgroup.
"""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from dataclasses import dataclass
from functools import cache
from pathlib import Path

logger = logging.getLogger(__name__)

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
    if not _CSV_PATH.is_file():
        logger.warning(
            "fund ranking CSV missing at %s — rebalancing will use held funds "
            "only for subgroup targets. Add Reference_docs/Prozpr_fund_ranking.csv "
            "for house recommended funds.",
            _CSV_PATH,
        )
        return {}

    by_sg: dict[str, list[FundRankRow]] = defaultdict(list)
    with open(_CSV_PATH, newline="", encoding="utf-8-sig") as f:
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
