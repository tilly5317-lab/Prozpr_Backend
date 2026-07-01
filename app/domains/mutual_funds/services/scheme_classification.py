"""Canonical mutual-fund classifier.

One function answers the question "what is this fund?" for the whole codebase.
Three tiers, derived from the SEBI sub-category:

    sub_category        →   asset_subgroup        →   asset_class
    (45 canonical labels)   (20 internal labels)      ("Equity" / "Debt" / "Others")
    "Liquid Fund"       →   "near_debt"           →   "Debt"
    "Large Cap Fund"    →   "low_beta_equities"   →   "Equity"
    "Gold ETF"          →   "gold_commodities"    →   "Others"

The mapping table is lifted from
``MF_Logics/Mututal_Funds_data_extraction/generate_mf_subgroup_mapping.py`` —
that legacy script ran once on an AMFI dump and produced
``mf_subgroup_mapped.csv``, which the rebalancing engine still consumes via
``rebal_engine/_disk_cache.py``. The CSV is a frozen output of the dict below;
this module is the live source.

Callers:
  * CAMS PDF ingest         (``ingestion/services/cams_cas_ingest.py``)
  * SimBanks XML sync       (``ingestion/services/simbanks_service.py``)
  * Portfolio REST response (``portfolio/routers/portfolio_router.py``)
  * Rebalancing engine      (``rebal_engine/_disk_cache.py``, future migration)

Entry point: ``classify_holding(sub_category, scheme_name)`` — handles SEBI
lookup plus the ``arbitrage_plus_income`` name-based override.

Fallback: ``resolve_asset_bucket(scheme_type, scheme_name)`` — last-resort
asset_class-only inferrer for holdings with no sub_category (direct stocks,
non-MF ETFs, or funds whose metadata sync hasn't happened yet).
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public vocabulary
# ---------------------------------------------------------------------------
#
# Title-case, three-bucket top-level vocabulary. Matches ``PortfolioAllocation
# .asset_class`` values already in the DB (CAMS/SimBanks write capitalized).
# AI engine + allocation engines historically used lowercase ``equity`` /
# ``debt`` / ``others`` — they'll need a thin adapter on their boundary, or be
# updated to this vocab in a later pass.
#
# Legacy ``mf_subgroup_mapped.csv`` uses ``equities`` (plural); we normalize
# to ``Equity`` (singular) which is what the DB column already stores.
ASSET_CLASS_EQUITY = "Equity"
ASSET_CLASS_DEBT = "Debt"
ASSET_CLASS_OTHERS = "Others"

VALID_ASSET_CLASSES: frozenset[str] = frozenset(
    {ASSET_CLASS_EQUITY, ASSET_CLASS_DEBT, ASSET_CLASS_OTHERS}
)


# ---------------------------------------------------------------------------
# Master mapping: canonical sub_category → (asset_class, asset_subgroup)
# ---------------------------------------------------------------------------
#
# Lifted verbatim from
# ``MF_Logics/Mututal_Funds_data_extraction/generate_mf_subgroup_mapping.py:8``
# with two normalizations:
#   * ``equities`` → ``equity`` (vocabulary alignment with AI engine).
#   * ``'Others (Index/ETF)'`` had a blank ``asset_class`` in legacy (likely a
#     bug); we fill it as ``others``.
# ``asset_class_sebi`` (e.g. ``equity_schemes``) is dropped — it duplicates
# ``asset_class`` semantically; one vocabulary is enough.
SUBCAT_TO_MAPPING: dict[str, tuple[str, str]] = {
    # Equity
    "Multi Cap Fund": ("Equity", "medium_beta_equities"),
    "Large Cap Fund": ("Equity", "low_beta_equities"),
    "Large & Mid Cap Fund": ("Equity", "medium_beta_equities"),
    "Mid Cap Fund": ("Equity", "medium_beta_equities"),
    "Small Cap Fund": ("Equity", "high_beta_equities"),
    "Flexi Cap Fund": ("Equity", "medium_beta_equities"),
    "Dividend Yield Fund": ("Equity", "dividend_equities"),
    "Value Fund": ("Equity", "value_equities"),
    "Contra Fund": ("Equity", "value_equities"),
    "Focused Fund": ("Equity", "high_beta_equities"),
    "Sectoral Fund": ("Equity", "sector_equities"),
    "Thematic Fund": ("Equity", "sector_equities"),
    "ELSS Tax Saver Fund": ("Equity", "tax_efficient_equities"),
    # Debt
    "Overnight Fund": ("Debt", "near_debt"),
    "Liquid Fund": ("Debt", "near_debt"),
    "Ultra Short Fund (3-6 months)": ("Debt", "near_debt"),
    "Ultra Short to Short Term Fund (6-12 months)": ("Debt", "short_debt"),
    "Money Market Fund": ("Debt", "short_debt"),
    "Short Term Fund (1-3 years)": ("Debt", "short_debt"),
    "Medium Term Fund (3-4 years)": ("Debt", "medium_debt"),
    "Medium Term to Long Term Fund (4-7 years)": ("Debt", "medium_debt"),
    "Long Term Fund (above 7 years)": ("Debt", "long_duration_debt"),
    "Dynamic Term Fund": ("Debt", "medium_debt"),
    "Corporate Bond Fund": ("Debt", "high_risk_debt"),
    "Credit Risk Fund": ("Debt", "high_risk_debt"),
    "Banking and PSU Debt Fund": ("Debt", "high_risk_debt"),
    "Gilt Fund": ("Debt", "medium_debt"),
    "10-Year Constant Maturity Gilt Fund": ("Debt", "long_duration_debt"),
    "Floating Interest Rates Fund": ("Debt", "floating_debt"),
    "Sectoral Debt Fund": ("Debt", "high_risk_debt"),
    # Hybrid — legacy keeps Conservative/Balanced under "Debt" and Aggressive
    # under "Equity" (asset_class column); we preserve that.
    # Subgroup change from legacy: Conservative + Balanced Hybrid were
    # ``"Others"`` (capitalized) in the legacy CSV — an orphan value not
    # recognized by any engine (STEP4_SUBGROUPS / _SUBGROUP_TO_ASSET_CLASS).
    # Their debt sleeve is short-to-medium duration in practice; map to
    # ``short_debt`` so they actually participate in debt rebalancing.
    "Conservative Hybrid Fund (Equity 10-25%, Debt 75-90%)": ("Debt", "short_debt"),
    "Balanced Hybrid Fund (Equity 40-60%, Debt 40-60%)": ("Debt", "short_debt"),
    "Aggressive Hybrid Fund (Equity 65-80%, Debt 20-35%)": (
        "Equity",
        "medium_beta_equities",
    ),
    "Dynamic Asset Allocation Fund": ("Equity", "medium_beta_equities"),
    "Multi-Asset Allocation Fund": ("Equity", "medium_beta_equities"),
    # Arbitrage funds are equity-taxed but debt-risk — engines treat them as
    # debt (see ai_engine/common.py:45). Legacy had them under "Others/others";
    # corrected here. The composite ``arbitrage_plus_income`` subgroup (used
    # by the practical engine for income-leaning debt) is fund-level and
    # assigned via the rebalancing fund-ranking CSV — not from SEBI sub_category.
    "Arbitrage Fund": ("Debt", "arbitrage"),
    # Index / ETF
    "Large Cap Index Linked (Index/ETF)": ("Equity", "low_beta_equities"),
    "Multi Cap Index Linked (Index/ETF)": ("Equity", "medium_beta_equities"),
    "Sectoral & Thematic Index Linked (Index/ETF)": ("Equity", "sector_equities"),
    "Gold Linked (Index/ETF)": ("Others", "gold_commodities"),
    "Silver Linked (Index/ETF)": ("Others", "silver_commodities"),
    "Others (Index/ETF)": ("Others", "others"),
    # Debt-index trackers (gilt / SDL / IBX / G-Sec / target-maturity). Duration
    # isn't reliably parseable from the name, so use the generic debt subgroup.
    "Debt Index Linked (Index/ETF)": ("Debt", "debt_subgroup"),
    # FoF
    "US Linked (FoF)": ("Equity", "us_equities"),
    "China Linked (FoF)": ("Equity", "china_equities"),
    "Others (FoF)": ("Others", "others_fofs"),
}


# ---------------------------------------------------------------------------
# asset_subgroup → asset_class
# ---------------------------------------------------------------------------
#
# Derived from SUBCAT_TO_MAPPING — every subgroup there already implies exactly
# one asset_class, so this map can never drift from the sub_category table.
#
# A few subgroups are produced by the *allocation engine* (not the SEBI
# classifier) and so don't appear in SUBCAT_TO_MAPPING. They're added
# explicitly here:
#   * ``multi_asset``           — engine's multi-asset equity sleeve
#   * ``arbitrage_plus_income`` — engine's income-leaning debt sleeve
#   * ``debt_subgroup``         — engine's generic debt sleeve
#   * ``gold``                  — engine's gold label (vs the classifier's
#                                 ``gold_commodities``)
#
# Three more come from the rebalancing fund-ranking CSV (its vocabulary, not
# the SEBI classifier's). Without them, funds in these subgroups silently fell
# through asset_class_for_subgroup's default into ``Others``:
#   * ``other_debt``     — CSV's generic debt bucket
#   * ``sector_debt``    — CSV's sectoral debt bucket
#   * ``other_equities`` — CSV's residual equity bucket
_ENGINE_ONLY_SUBGROUP_TO_ASSET_CLASS: dict[str, str] = {
    "multi_asset": ASSET_CLASS_EQUITY,
    "arbitrage_plus_income": ASSET_CLASS_DEBT,
    "debt_subgroup": ASSET_CLASS_DEBT,
    "gold": ASSET_CLASS_OTHERS,
    "other_debt": ASSET_CLASS_DEBT,
    "sector_debt": ASSET_CLASS_DEBT,
    "other_equities": ASSET_CLASS_EQUITY,
}


def _build_subgroup_to_asset_class() -> dict[str, str]:
    out: dict[str, str] = {sg: ac for ac, sg in SUBCAT_TO_MAPPING.values()}
    out.update(_ENGINE_ONLY_SUBGROUP_TO_ASSET_CLASS)
    return out


SUBGROUP_TO_ASSET_CLASS: dict[str, str] = _build_subgroup_to_asset_class()


def asset_class_for_subgroup(subgroup: Optional[str]) -> str:
    """Map an ``asset_subgroup`` → canonical asset_class (``Equity`` / ``Debt`` / ``Others``).

    Unknown / empty → ``Others``. This is the single source for the
    subgroup→class direction; the rebalancing response and the chat
    asset-class summary both read from here.
    """
    if not subgroup:
        return ASSET_CLASS_OTHERS
    return SUBGROUP_TO_ASSET_CLASS.get(subgroup.strip(), ASSET_CLASS_OTHERS)


# ---------------------------------------------------------------------------
# Raw → canonical sub_category normalizations
# ---------------------------------------------------------------------------
#
# ``MfFundMetadata.sub_category`` stores whatever ingest wrote — typically the
# raw scheme_type / scheme_category string from CAMS/SimBanks/AMFI. Some of
# those strings need a tiny rewrite before they line up with the canonical
# labels in SUBCAT_TO_MAPPING.
#
# Only NON-IDENTITY mappings live here. Raw inputs that already match a key
# in SUBCAT_TO_MAPPING (e.g. "Large Cap Fund", "Liquid Fund") flow through
# untouched via the ``canonical = raw`` fallback in ``classify_sub_category``.
_RAW_SUBCAT_NORMALIZATIONS: dict[str, str] = {
    "Sectoral/ Thematic": "Sectoral Fund",
    "Sectoral/Thematic": "Sectoral Fund",
    "ELSS": "ELSS Tax Saver Fund",
    "Ultra Short Duration Fund": "Ultra Short Fund (3-6 months)",
    "Low Duration Fund": "Ultra Short to Short Term Fund (6-12 months)",
    "Money Market": "Money Market Fund",
    "Short Duration Fund": "Short Term Fund (1-3 years)",
    "Medium Duration Fund": "Medium Term Fund (3-4 years)",
    "Medium to Long Duration Fund": "Medium Term to Long Term Fund (4-7 years)",
    "Long Duration Fund": "Long Term Fund (above 7 years)",
    "Dynamic Bond": "Dynamic Term Fund",
    "Banking and PSU Fund": "Banking and PSU Debt Fund",
    "Gilt Fund with 10 year constant duration": "10-Year Constant Maturity Gilt Fund",
    "Floater Fund": "Floating Interest Rates Fund",
    "Conservative Hybrid Fund": "Conservative Hybrid Fund (Equity 10-25%, Debt 75-90%)",
    "Balanced Hybrid Fund": "Balanced Hybrid Fund (Equity 40-60%, Debt 40-60%)",
    "Aggressive Hybrid Fund": "Aggressive Hybrid Fund (Equity 65-80%, Debt 20-35%)",
    "Dynamic Asset Allocation or Balanced Advantage": "Dynamic Asset Allocation Fund",
    "Multi Asset Allocation": "Multi-Asset Allocation Fund",
    "Gold ETF": "Gold Linked (Index/ETF)",
}

# Sub-categories the legacy script left intentionally blank (no canonical
# bucketing yet). Callers should treat ``classify_sub_category`` returning
# ``(None, None)`` for these as "unknown" and fall through to
# ``resolve_asset_bucket``.
_BLANK_SUBCATS: frozenset[str] = frozenset(
    {"Children’s Fund", "Retirement Fund", "Equity Savings", "IDF", "Income"}
)


# ---------------------------------------------------------------------------
# Name-pattern dispatch for SEBI categories that need fund-name analysis
# ---------------------------------------------------------------------------
#
# SEBI's ``"Index Funds"`` / ``"Other  ETFs"`` / ``"FoF Overseas"`` /
# ``"FoF Domestic"`` sub-categories are too coarse to map directly — a single
# label covers Nifty 50 trackers, gold ETFs, US FoFs, debt-index ETFs, etc.
# Lifted verbatim from ``MF_Logics/generate_mf_subgroup_mapping.py:117-230``.

_DEBT_INDEX_PATTERNS: tuple[str, ...] = (
    r"\bcrisil\b",
    r"\bibx\b",
    r"\bgilt\b",
    r"\bsdl\b",
    r"\bg-sec\b",
    r"\bgsec\b",
    r"treasury\s*bill",
    r"\baaa\b",
    r"debt\s*(index|passive)",
    r"bond\s*(index|etf)",
    r"overnight\s*rate",
    r"\b1d\s*rate\b",
    r"target\s*maturity",
    r"bharat\s*bond",
    r"cpse\s*bond",
    r"\bliquid\s*(rate|overnight)\b",
    r"\bbse\s*liquid\s*rate\b",
    r"\bnifty\s*psu\s*bond\b",
)

_LARGE_CAP_PATTERNS: tuple[str, ...] = (
    r"\bnifty\s*50\b(?!\d)",
    r"\bsensex\b(?!\s*next)",
    r"\bnifty\s*100\b",
    r"\bbse\s*100\b",
    r"\bbse\s*30\b",
    r"\bnifty\s+index\b",
    r"\bnifty\s+etf\b",
    r"\bnifty\s+exchange\b",
    r"\btop\s*10\s*equal\b",
    r"\btop\s*20\s*equal\b",
    r"\btop\s*15\s*equal\b",
)

_SECTORAL_PATTERNS: tuple[str, ...] = (
    r"\bpharma\b",
    r"\bhealthcare\b",
    r"\bhealth\s*care\b",
    r"(?:nifty|bse)\s+it\b",
    r"\btechnology\b",
    r"\binfra\b",
    r"\binfrastructure\b",
    r"\bbank\b",
    r"\bbanking\b",
    r"\bfinancial\s*services?\b",
    r"\bfinserv\b",
    r"\bdefence\b",
    r"\bdefense\b",
    r"\bauto\b",
    r"\bautomobile\b",
    r"\bautomotive\b",
    r"\bfmcg\b",
    r"\bconsumption\b",
    r"\bconsumer\b",
    r"\benergy\b",
    r"\boil\b",
    r"\bpse\b",
    r"\bpsu\b",
    r"\bcpse\b",
    r"\bmanufacturing\b",
    r"\breal\s*estate\b",
    r"\breit\b",
    r"\brealty\b",
    r"\bev\b",
    r"\belectric\s*vehicle\b",
    r"\bdigital\b",
    r"\bmnc\b",
    r"\bprivate\s*bank\b",
    r"\bmedia\b",
    r"\bmetal\b",
    r"\btelecom\b",
    r"\bshariah\b",
    r"\bmobility\b",
    r"\bnon.?cyclical\b",
    r"\bcyclical\b",
    r"\bhousehold\b",
    r"\btourism\b",
    r"\bhousing\b",
    r"\bnatural\s*resources?\b",
    r"\bcommodit",
    r"\bcapital\s*markets?\b",
    r"\bprivate\s*sector\b",
    r"\btransportation\b",
    r"\blogistics\b",
    r"\bsemiconductor\b",
    r"\binternet\b",
    r"\bservices\s+sector\b",
    r"\bchemical\b",
    r"\bchemicals\b",
    r"\bpower\b",
    r"\brailway\b",
    r"\bhospital\b",
    r"\bipu\b",
    r"\bgrowth\s*sectors?\s*15\b",
    r"\bdividend\s*opportunities\b",
    r"\bselect\s*ipo\b",
    r"\bnew\s*age\b",
    r"\bnifty\s+it\b",
    r"\besg\b",
    r"\bbharat\s*22\b",
)

_INTERNATIONAL_PATTERNS: tuple[str, ...] = (
    r"\bnasdaq\b",
    r"\bnyse\b",
    r"\bhang\s*seng\b",
    r"\bs&p\s*500\b",
)

_US_FOF_PATTERNS: tuple[str, ...] = (
    r"\bus\b",
    r"\bnasdaq\b",
    r"\bs&p\s*500\b",
    r"\bamerica\b",
)

_CHINA_FOF_PATTERNS: tuple[str, ...] = (
    r"\bchina\b",
    r"\bhang\s*seng\b",
)


def _match_any(patterns: tuple[str, ...], text: str) -> bool:
    return any(re.search(p, text) for p in patterns)


def _classify_index_or_etf(scheme_name: Optional[str]) -> str:
    """Map an Index Fund / ETF scheme name → canonical sub_category.

    Debt-index trackers (gilt / SDL / IBX / G-Sec / target-maturity / Bharat
    Bond, etc.) → ``"Debt Index Linked (Index/ETF)"`` so they bucket as Debt.
    Their names often contain "Nifty"/"BSE"/index words that would otherwise
    trip the equity heuristics, so debt detection must run first.
    """
    name = (scheme_name or "").lower()
    if not name:
        return ""
    if _match_any(_DEBT_INDEX_PATTERNS, name):
        return "Debt Index Linked (Index/ETF)"
    if re.search(r"\bgold\b", name):
        return "Gold Linked (Index/ETF)"
    if re.search(r"\bsilver\b", name):
        return "Silver Linked (Index/ETF)"
    if _match_any(_LARGE_CAP_PATTERNS, name):
        return "Large Cap Index Linked (Index/ETF)"
    if _match_any(_SECTORAL_PATTERNS, name):
        return "Sectoral & Thematic Index Linked (Index/ETF)"
    if _match_any(_INTERNATIONAL_PATTERNS, name):
        return "Others (Index/ETF)"
    return "Multi Cap Index Linked (Index/ETF)"


def _classify_fof_overseas(scheme_name: Optional[str]) -> str:
    name = (scheme_name or "").lower()
    if _match_any(_US_FOF_PATTERNS, name):
        return "US Linked (FoF)"
    if _match_any(_CHINA_FOF_PATTERNS, name):
        return "China Linked (FoF)"
    return "Others (FoF)"


def _classify_fof_domestic(scheme_name: Optional[str]) -> str:
    name = (scheme_name or "").lower()
    if re.search(r"\bgold\b", name):
        return "Gold Linked (Index/ETF)"
    if re.search(r"\bsilver\b", name):
        return "Silver Linked (Index/ETF)"
    return "Others (FoF)"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_sub_category(
    raw_sub_category: Optional[str],
    scheme_name: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Map a fund's SEBI sub_category → ``(asset_class, asset_subgroup)``.

    For SEBI labels that are too coarse on their own — ``"Index Funds"``,
    ``"Other  ETFs"`` (note the legacy double space), ``"FoF Overseas"``,
    ``"FoF Domestic"`` — pass ``scheme_name`` and the function dispatches by
    name pattern (gold/silver, large-cap index, sectoral, US/China FoF, etc.)
    to pick the right canonical sub_category before the SUBCAT_TO_MAPPING
    lookup.

    Returns ``(None, None)`` when input is empty, intentionally-blank, or not
    recognized. Callers should then fall back to ``resolve_asset_bucket``.
    """
    if not raw_sub_category:
        return (None, None)
    raw = raw_sub_category.strip()
    if raw in _BLANK_SUBCATS:
        return (None, None)
    # Tier 1: direct raw → canonical lookup (covers ~30 of 45 SEBI labels).
    canonical: Optional[str] = _RAW_SUBCAT_NORMALIZATIONS.get(raw)
    # Tier 2: name-based dispatch for Index/ETF/FoF SEBI categories.
    if canonical is None:
        if raw in ("Index Funds", "Other  ETFs"):
            canonical = _classify_index_or_etf(scheme_name)
        elif raw == "FoF Overseas":
            canonical = _classify_fof_overseas(scheme_name)
        elif raw == "FoF Domestic":
            canonical = _classify_fof_domestic(scheme_name)
        else:
            # Unknown raw label — try it verbatim against SUBCAT_TO_MAPPING
            # in case the caller already passed a canonical label.
            canonical = raw
    if not canonical:  # dispatcher returned "" → debt-index left unclassified
        return (None, None)
    pair = SUBCAT_TO_MAPPING.get(canonical)
    if pair is None:
        return (None, None)
    return pair


def is_income_plus_arbitrage_fund(scheme_name: Optional[str]) -> bool:
    """Detect FoFs that combine arbitrage with income (debt-like) strategies.

    These funds are SEBI-classified as ``"FoFs (Domestic)"`` (which our
    canonical mapping treats as ``(Others, others_fofs)``) but our internal
    taxonomy assigns them to the ``arbitrage_plus_income`` subgroup — the
    income-leaning debt bucket used by the practical asset-allocation engine
    (``AI_Agents/src/practical_asset_allocation/pipeline.py:500-502``).

    Detection is name-based. Canonical names contain "Income Plus Arbitrage"
    (the rebalancing fund-ranking CSV at
    ``AI_Agents/Reference_docs/prozpr_fund_ranking_may_2026.csv`` uses this
    same signal — top-ranked entries are HDFC / ICICI / Bandhan / ABSL
    "Income Plus Arbitrage Active FOF" schemes).
    """
    if not scheme_name:
        return False
    name = scheme_name.lower()
    return "income plus arbitrage" in name or "arbitrage plus income" in name


def classify_holding(
    sub_category: Optional[str],
    scheme_name: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Top-level classifier — SEBI sub_category with name-based overrides.

    Order of precedence:
      1. Name-based override for ``arbitrage_plus_income`` — these FoFs are
         SEBI-classified as generic "FoFs (Domestic)" but our taxonomy needs
         them in the debt/arbitrage_plus_income subgroup to stay aligned with
         the rebalancing engine.
      2. SEBI sub_category lookup via ``classify_sub_category``.

    Returns ``(None, None)`` when neither path produces a classification —
    callers should then fall back to ``resolve_asset_bucket(scheme_type,
    scheme_name)`` for an asset_class-only best guess (no subgroup).
    """
    if is_income_plus_arbitrage_fund(scheme_name):
        return (ASSET_CLASS_DEBT, "arbitrage_plus_income")
    return classify_sub_category(sub_category, scheme_name)


# ---------------------------------------------------------------------------
# Multi-asset / hybrid look-through (asset-class split for blended funds)
# ---------------------------------------------------------------------------
#
# A blended fund holds equity + debt (+ others) inside one scheme. The canonical
# classifier above maps each such fund to a SINGLE asset_class (e.g. a
# Multi-Asset Allocation Fund → Equity), which over-counts equity wherever
# holdings roll up into an Equity/Debt/Others mix. To match the asset-allocation
# engine's multi-asset composition methodology (surfaced in chat), these funds
# are instead SPLIT across asset classes by a per-category band.
#
# This table is the single source of the split rule; every asset-class rollup
# (portfolio donut, chat current-mix, rebalancing breakdown) routes through
# ``add_to_asset_class_mix`` so the split stays consistent app-wide. Keyed on
# CANONICAL sub_category (the SUBCAT_TO_MAPPING vocabulary); raw SEBI labels are
# normalized first. Weights within each row sum to 1.0.
ASSET_CLASS_LOOKTHROUGH_WEIGHTS: dict[str, dict[str, float]] = {
    "Multi-Asset Allocation Fund": {
        ASSET_CLASS_EQUITY: 0.725,
        ASSET_CLASS_DEBT: 0.125,
        ASSET_CLASS_OTHERS: 0.15,
    },
    "Aggressive Hybrid Fund (Equity 65-80%, Debt 20-35%)": {
        ASSET_CLASS_EQUITY: 0.725,
        ASSET_CLASS_DEBT: 0.175,
        ASSET_CLASS_OTHERS: 0.10,
    },
    "Dynamic Asset Allocation Fund": {
        ASSET_CLASS_EQUITY: 0.50,
        ASSET_CLASS_DEBT: 0.50,
    },
    "Balanced Hybrid Fund (Equity 40-60%, Debt 40-60%)": {
        ASSET_CLASS_EQUITY: 0.50,
        ASSET_CLASS_DEBT: 0.50,
    },
    "Conservative Hybrid Fund (Equity 10-25%, Debt 75-90%)": {
        ASSET_CLASS_EQUITY: 0.175,
        ASSET_CLASS_DEBT: 0.825,
    },
    # Flexi Cap is a pure-equity SEBI category (single-class in SUBCAT_TO_MAPPING
    # → medium_beta_equities); modeled here with a debt+others sleeve so the
    # asset-class look-through reflects its real-world cash/debt holdings. The
    # subgroup-level rebalancing still treats it as 100% equity.
    "Flexi Cap Fund": {
        ASSET_CLASS_EQUITY: 0.725,
        ASSET_CLASS_DEBT: 0.175,
        ASSET_CLASS_OTHERS: 0.10,
    },
}


def asset_class_lookthrough(sub_category: Optional[str]) -> Optional[dict[str, float]]:
    """Equity/Debt/Others weights for a blended fund whose value should be split
    across asset classes (look-through), or ``None`` when the fund maps cleanly
    to a single asset_class (caller should use ``classify_holding`` /
    ``asset_class_for_subgroup`` instead).

    Accepts raw or canonical ``sub_category`` — raw SEBI labels are normalized
    via ``_RAW_SUBCAT_NORMALIZATIONS`` (the same Tier-1 path
    ``classify_sub_category`` uses) before the lookup.
    """
    if not sub_category:
        return None
    raw = sub_category.strip()
    canonical = _RAW_SUBCAT_NORMALIZATIONS.get(raw, raw)
    return ASSET_CLASS_LOOKTHROUGH_WEIGHTS.get(canonical)


def add_to_asset_class_mix(
    mix: dict[str, float],
    *,
    amount: float,
    sub_category: Optional[str],
    fallback_asset_class: str,
) -> None:
    """Accumulate ``amount`` into ``mix`` (keyed by canonical asset_class),
    splitting blended funds via ``asset_class_lookthrough`` when applicable and
    otherwise assigning the whole amount to ``fallback_asset_class``.

    Mutates ``mix`` in place. This is the single entry point for the multi-asset
    / hybrid look-through — route every asset-class rollup through here so the
    split stays consistent across the portfolio donut, chat current-mix and the
    rebalancing breakdown.
    """
    weights = asset_class_lookthrough(sub_category)
    if weights:
        for asset_class, weight in weights.items():
            mix[asset_class] = mix.get(asset_class, 0.0) + amount * weight
    else:
        mix[fallback_asset_class] = mix.get(fallback_asset_class, 0.0) + amount


# ---------------------------------------------------------------------------
# Name-based fallback (when sub_category is missing/unknown)
# ---------------------------------------------------------------------------
#
# Lifted verbatim from ``cams_cas_ingest._bucket_from_name`` /
# ``resolve_asset_bucket``. Only behaviour change: legacy emitted ``"Cash"``
# for liquid/overnight/money_market name hits; we now emit ``"Debt"`` (the
# 3-bucket model has no Cash). Output vocabulary is title-case to match
# ``PortfolioAllocation.asset_class`` in the DB.

_DEBT_NAME_HINTS: tuple[str, ...] = (
    # Cash-like names that previously routed to "Cash" — now folded into Debt.
    "liquid",
    "overnight",
    "money market",
    "cash management",
    # Duration-style debt names.
    "debt",
    "bond",
    "gilt",
    "g-sec",
    "gsec",
    "g sec",
    "income fund",
    "corporate bond",
    "credit risk",
    "banking & psu",
    "banking and psu",
    "psu debt",
    "psu bond",
    "dynamic bond",
    "floater",
    "floating rate",
    "short term",
    "short duration",
    "low duration",
    "ultra short",
    "medium term",
    "medium duration",
    "long duration",
    "constant maturity",
    "fixed maturity",
    "fmp",
    "interval fund",
    "10 year",
    "duration fund",
)

_OTHERS_NAME_HINTS: tuple[str, ...] = (
    # Hybrids, arbitrage, multi-asset. Legacy puts Conservative/Balanced
    # Hybrid in "debt" (asset_class), but their *names* alone can't tell us
    # which kind of hybrid; default name-only hybrids to "others".
    "hybrid",
    "balanced",
    "asset allocation",
    "multi asset",
    "multi-asset",
    "equity savings",
    "arbitrage",
    "dynamic asset",
    "conservative",
    "aggressive",
    "regular savings",
    "equity & debt",
    "equity and debt",
    "balanced advantage",
    "income & growth",
    "capital protection",
    # Commodities / gold / silver.
    "gold",
    "silver",
    "commodity",
    "commodities",
)

_EQUITY_NAME_HINTS: tuple[str, ...] = (
    "equity",
    "elss",
    "tax saver",
    "taxsaver",
    "tax plan",
    "bluechip",
    "blue chip",
    "flexi cap",
    "flexicap",
    "multi cap",
    "multicap",
    "large cap",
    "largecap",
    "large & mid",
    "large and mid",
    "mid cap",
    "midcap",
    "small cap",
    "smallcap",
    "micro cap",
    "microcap",
    "focused",
    "value fund",
    "contra",
    "dividend yield",
    "opportunit",
    "special situation",
    "nifty",
    "sensex",
    "nasdaq",
    "s&p",
    "msci",
    "index fund",
    "etf",
    "top 100",
    "top 200",
    "top 250",
    "top 500",
    "consumption",
    "infrastructure",
    "infra ",
    "banking & financial",
    "banking and financial",
    "financial services",
    "pharma",
    "healthcare",
    "technology",
    "digital",
    "fmcg",
    "mnc",
    "psu equity",
    "manufacturing",
    "transportation",
    "logistics",
    "business cycle",
    "quant fund",
    "momentum",
    "esg",
    "quality fund",
    "alpha",
    "growth fund",
    "emerging",
    "long term equity",
    "capital builder",
    "wealth builder",
    "bharat",
    "india fund",
    "core equity",
    "prime equity",
    "active fund",
    "champions",
    "leaders",
    "frontline",
    "discovery",
    "exploration",
    "innovation",
    "world fund",
    "global ",
    "international",
    "us equity",
    "china",
    "japan",
    "europe",
    "energy opportunit",
)

# casparser ``type`` values meaning "I couldn't classify it" — trigger name fallback.
_UNKNOWN_TYPE_TOKENS: frozenset[str] = frozenset(
    {"", "N/A", "NA", "UNKNOWN", "OTHER", "NONE"}
)


def _bucket_from_name(scheme_name: Optional[str]) -> Optional[str]:
    """Best-effort asset-class guess from a scheme's *name*. ``None`` if undecidable.

    Order matters — more specific vocabulary first. "...Equity & Debt Fund"
    must hit the hybrid hints before the equity hints; "Gold Savings Fund"
    must hit commodities before any name containing "savings"; etc.
    """
    name = (scheme_name or "").lower()
    if not name:
        return None
    if any(h in name for h in _OTHERS_NAME_HINTS):
        return ASSET_CLASS_OTHERS
    if any(h in name for h in _DEBT_NAME_HINTS):
        return ASSET_CLASS_DEBT
    if any(h in name for h in _EQUITY_NAME_HINTS):
        return ASSET_CLASS_EQUITY
    return None


def resolve_asset_bucket(
    scheme_type: Optional[str],
    scheme_name: Optional[str],
) -> str:
    """Last-resort asset-class inferrer when canonical sub_category is unavailable.

    Two passes:
      1. Trust the upstream ``scheme_type`` field when it actually classified
         the scheme (CAS / AMFI / SimBanks all expose a type-ish string).
      2. Otherwise infer from the scheme name — the name almost always says
         what kind of fund it is ("Flexi Cap", "Nifty 50 Index", "Liquid",
         "Gilt", …).

    Falls back to ``"others"`` only when both passes fail.
    """
    t = (scheme_type or "").strip().upper()
    if t not in _UNKNOWN_TYPE_TOKENS:
        if "EQUITY" in t:
            return ASSET_CLASS_EQUITY
        # Liquid / Money / Overnight previously routed to "Cash"; now folded into Debt.
        if any(k in t for k in ("LIQUID", "MONEY", "OVERNIGHT", "CASH", "TREASURY")):
            return ASSET_CLASS_DEBT
        if any(
            k in t
            for k in ("DEBT", "BOND", "GILT", "INCOME", "DURATION", "GSEC", "G-SEC")
        ):
            return ASSET_CLASS_DEBT
        if any(
            k in t
            for k in (
                "HYBRID",
                "BALANCED",
                "ARBITRAGE",
                "FOF",
                "SOLUTION",
                "GOLD",
                "COMMODITY",
                "MULTI ASSET",
            )
        ):
            return ASSET_CLASS_OTHERS
    guessed = _bucket_from_name(scheme_name)
    if guessed is not None:
        return guessed
    logger.info(
        "scheme_classification: could not classify %r (type=%r) → bucketed as %r",
        scheme_name,
        scheme_type,
        ASSET_CLASS_OTHERS,
    )
    return ASSET_CLASS_OTHERS


# ---------------------------------------------------------------------------
# Response-builder convenience: trust existing values, fill the rest
# ---------------------------------------------------------------------------


def fill_classification(
    sub_category: Optional[str],
    scheme_name: Optional[str],
    *,
    asset_class: Optional[str] = None,
    asset_subgroup: Optional[str] = None,
    scheme_type: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Return ``(asset_class, asset_subgroup)``, trusting any values already
    supplied and deriving the rest from this canonical module.

    API response builders use this to honor the rule: keep the classification
    the data already carries (e.g. a curated ``MfFundRating`` value); when it's
    absent, compute it here rather than leaving the field blank for the client.

    Resolution for a missing ``asset_class`` / ``asset_subgroup``:
      1. ``classify_holding(sub_category, scheme_name)`` — SEBI sub_category
         lookup plus the ``arbitrage_plus_income`` name override (yields both).
      2. ``resolve_asset_bucket(scheme_type or sub_category, scheme_name)`` —
         name-based last resort (asset_class only; subgroup stays ``None``).
    """
    ac, asg = asset_class, asset_subgroup
    if ac is None or asg is None:
        guess_ac, guess_asg = classify_holding(sub_category, scheme_name)
        if ac is None:
            ac = guess_ac
        if asg is None:
            asg = guess_asg
    if ac is None:
        ac = resolve_asset_bucket(scheme_type or sub_category, scheme_name)
    return ac, asg
