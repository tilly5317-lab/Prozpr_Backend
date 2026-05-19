"""CSV-backed NAV + fund-metadata reader for the rebalancing engine.

TODO(DB-backed): This module currently reads MF NAV and fund metadata from
local CSV files under ``MF_Logics/Mututal_Funds_data_extraction/``. The
production target is to read these from the ``mf_nav_history`` and
``mf_fund_metadata`` DB tables (populated by ``app/services/mf/mfapi_ingest``).
When those tables are kept fresh in prod, replace the function bodies here
with the DB queries that used to live in ``input_builder.py`` /
``holdings_ledger.py`` and remove the CSV files from the engine's input set.

This is the **only** path the rebalancing engine uses to look up NAV and fund
metadata. Tests inject controlled fixture data by monkeypatching the
``latest_nav_by_isin``, ``metadata_by_isin``, and ``scheme_to_isin`` functions
on this module — they do not touch the CSV files.
"""

from __future__ import annotations

import csv
import functools
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Optional


_REPO_ROOT = Path(__file__).resolve().parents[4]
_NAV_CSV = _REPO_ROOT / "MF_Logics" / "Mututal_Funds_data_extraction" / "latest_nav_active.csv"
_META_CSV = _REPO_ROOT / "MF_Logics" / "Mututal_Funds_data_extraction" / "mf_subgroup_mapped.csv"


@dataclass(frozen=True)
class CsvFundMetadata:
    """Mirror of the subset of ``MfFundMetadata`` columns the engine reads."""

    scheme_code: str
    scheme_name: str
    asset_class: Optional[str]
    asset_subgroup: Optional[str]
    sub_category: Optional[str]
    exit_load_percent: Optional[Decimal] = None
    exit_load_months: Optional[int] = None


@functools.lru_cache(maxsize=1)
def _load_nav_table() -> dict[str, Decimal]:
    """ISIN → latest NAV. Both growth and div-reinvest ISINs are indexed."""
    out: dict[str, Decimal] = {}
    with _NAV_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            nav_str = row.get("nav") or ""
            if not nav_str.strip():
                continue
            try:
                nav = Decimal(nav_str)
            except Exception:
                continue
            for key in ("isinGrowth", "isinDivReinvestment"):
                isin = (row.get(key) or "").strip()
                if isin:
                    out[isin] = nav
    return out


@functools.lru_cache(maxsize=1)
def _load_meta_table() -> tuple[dict[str, CsvFundMetadata], dict[str, str]]:
    """Returns ``(isin → CsvFundMetadata, scheme_code → primary_isin)``."""
    by_isin: dict[str, CsvFundMetadata] = {}
    scheme_to_isin: dict[str, str] = {}
    with _META_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            scheme_code = (row.get("schemeCode") or "").strip()
            growth = (row.get("isinGrowth") or "").strip()
            div = (row.get("isinDivReinvestment") or "").strip()
            if not scheme_code:
                continue
            meta = CsvFundMetadata(
                scheme_code=scheme_code,
                scheme_name=(row.get("schemeName") or "").strip(),
                asset_class=(row.get("asset_class") or None) or None,
                asset_subgroup=(row.get("asset_subgroup") or None) or None,
                sub_category=(row.get("sub_category") or None) or None,
            )
            primary = growth or div
            if primary and scheme_code not in scheme_to_isin:
                scheme_to_isin[scheme_code] = primary
            for isin in (growth, div):
                if isin and isin not in by_isin:
                    by_isin[isin] = meta
    return by_isin, scheme_to_isin


def latest_nav_by_isin(isins: set[str]) -> dict[str, Decimal]:
    """ISIN → NAV for the requested ISINs (missing ISINs are simply absent)."""
    if not isins:
        return {}
    table = _load_nav_table()
    return {isin: nav for isin, nav in ((i, table.get(i)) for i in isins) if nav is not None}


def metadata_by_isin(isins: set[str]) -> dict[str, CsvFundMetadata]:
    """ISIN → metadata for the requested ISINs."""
    if not isins:
        return {}
    table, _ = _load_meta_table()
    return {isin: table[isin] for isin in isins if isin in table}


def scheme_to_isin(scheme_codes: set[str]) -> dict[str, str]:
    """scheme_code → primary ISIN (growth plan preferred, dividend-reinvest fallback)."""
    if not scheme_codes:
        return {}
    _, table = _load_meta_table()
    return {code: table[code] for code in scheme_codes if code in table}


def _reset_cache() -> None:
    """Test helper: clear the lazy CSV cache between tests if a test mutates the source files."""
    _load_nav_table.cache_clear()
    _load_meta_table.cache_clear()
