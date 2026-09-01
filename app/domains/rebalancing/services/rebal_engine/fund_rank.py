# app/services/ai_bridge/rebalancing/fund_rank.py
"""Loader for the static fund-rank CSV consumed by the rebalancing input builder.

The CSV has two row types:

* Rank populated (1, 2, …) → a recommended fund. `selection_reason` carries the
  positive "why we picked this" string.
* Rank blank → a fund the data team evaluated but rejected. One or more of the
  9 ``*_reason`` columns explain why; ``get_rejection_reasons`` joins them.

If the file is absent (typical in fresh clones), :func:`get_fund_ranking` returns
an empty mapping; the input builder then derives rank-1 targets from held funds
per allocation subgroup.

Both views are loaded once at module import time and cached as frozen dicts;
no DB calls.
"""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from dataclasses import dataclass
from functools import cache
from pathlib import Path

logger = logging.getLogger(__name__)

# app/domains/rebalancing/services/rebal_engine/fund_rank.py:
# parents[0]=rebal_engine, [1]=services, [2]=rebalancing, [3]=domains,
# [4]=app, [5]=repo root (where AI_Agents/ lives).
_CSV_PATH = (
    Path(__file__).resolve().parents[5]
    / "AI_Agents"
    / "Reference_docs"
    / "prozpr_fund_ranking_june_2026_v2.csv"
)


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


# Sentinel rank marking explicitly-bad funds the team wants force-exited
# regardless of tax cost. Distinct from blank-rank rows (evaluated-and-skipped,
# which become NEUTRAL — held but not traded) and from real ranks ≥ 1
# (recommended picks).
FORCE_EXIT_RANK = 9999


@dataclass(frozen=True)
class FundRankRow:
    asset_subgroup: str
    sub_category: str
    rank: int
    isin: str
    fund_name: str
    selection_reason: str = ""
    scheme_code: str = ""


@cache
def get_fund_ranking() -> dict[str, list[FundRankRow]]:
    """Return ``{asset_subgroup: [FundRankRow, ...]}`` for recommended funds
    (rank ≥ 1 and rank != FORCE_EXIT_RANK), sorted by rank.

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
            rank_raw = (row.get("rank") or "").strip()
            if not rank_raw:
                continue
            rank_int = int(rank_raw)
            if rank_int == FORCE_EXIT_RANK:
                continue
            by_sg[row["asset_subgroup"]].append(
                FundRankRow(
                    asset_subgroup=row["asset_subgroup"],
                    sub_category=row["sub_category"],
                    rank=rank_int,
                    isin=row["isin"],
                    fund_name=row["recommended_fund"],
                    selection_reason=(row.get("selection_reason") or "").strip(),
                    scheme_code=(row.get("scheme_code") or "").strip(),
                )
            )
    for subgroup in by_sg:
        by_sg[subgroup].sort(key=lambda r: r.rank)
    return dict(by_sg)


@cache
def get_all_rows() -> list[FundRankRow]:
    """Every CSV row: recommended AND evaluated-but-rejected (rank-blank rows
    carry ``rank=0``); force-exit rows are excluded. Same cache/reload contract
    as ``get_fund_ranking``.
    """
    if not _CSV_PATH.is_file():
        return []
    out: list[FundRankRow] = []
    with open(_CSV_PATH, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rank_raw = (row.get("rank") or "").strip()
            rank_int = int(rank_raw) if rank_raw else 0
            if rank_int == FORCE_EXIT_RANK:
                continue
            out.append(
                FundRankRow(
                    asset_subgroup=row["asset_subgroup"],
                    sub_category=row["sub_category"],
                    rank=rank_int,
                    isin=row["isin"],
                    fund_name=row["recommended_fund"],
                    selection_reason=(row.get("selection_reason") or "").strip(),
                    scheme_code=(row.get("scheme_code") or "").strip(),
                )
            )
    return out


@cache
def get_force_exit_isins() -> set[str]:
    """Return the set of ISINs flagged as force-exit in the ranking CSV
    (rank == ``FORCE_EXIT_RANK``). Held funds matching these will be
    liquidated by the engine regardless of tax cost.
    """
    if not _CSV_PATH.is_file():
        return set()
    out: set[str] = set()
    with open(_CSV_PATH, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rank_raw = (row.get("rank") or "").strip()
            if not rank_raw:
                continue
            if int(rank_raw) == FORCE_EXIT_RANK:
                out.add(row["isin"])
    return out


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
