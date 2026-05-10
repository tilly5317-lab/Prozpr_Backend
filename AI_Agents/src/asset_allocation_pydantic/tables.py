from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AssetClassBounds:
    eq_min: int
    eq_max: int
    debt_min: int
    debt_max: int
    others_min: int
    others_max: int


# ── Phase 1 — asset class min/max by effective_risk_score ─────────────────────
# Source: references/long-term-goals.md lines 46-66
PHASE1_RISK_BOUNDS: dict[float, AssetClassBounds] = {
    10.0: AssetClassBounds(70, 100, 0, 20, 0, 10),
    9.5:  AssetClassBounds(65, 100, 0, 25, 0, 10),
    9.0:  AssetClassBounds(60, 100, 0, 30, 0, 10),
    8.5:  AssetClassBounds(60,  90, 5, 30, 0, 10),
    8.0:  AssetClassBounds(55,  85, 10, 30, 0, 15),
    7.5:  AssetClassBounds(50,  80, 10, 35, 5, 15),
    7.0:  AssetClassBounds(45,  75, 15, 40, 5, 15),
    6.5:  AssetClassBounds(40,  70, 20, 45, 5, 15),
    6.0:  AssetClassBounds(35,  65, 25, 50, 5, 15),
    5.5:  AssetClassBounds(30,  60, 30, 55, 5, 15),
    5.0:  AssetClassBounds(30,  60, 30, 60, 5, 15),
    4.5:  AssetClassBounds(25,  55, 35, 65, 5, 15),
    4.0:  AssetClassBounds(20,  55, 35, 70, 5, 15),
    3.5:  AssetClassBounds(15,  50, 40, 70, 5, 15),
    3.0:  AssetClassBounds(15,  45, 40, 75, 5, 15),
    2.5:  AssetClassBounds(10,  45, 40, 80, 5, 15),
    2.0:  AssetClassBounds(10,  40, 40, 85, 5, 15),
    1.5:  AssetClassBounds( 5,  35, 45, 90, 5, 15),
    1.0:  AssetClassBounds( 5,  30, 45, 95, 5, 15),
}


# ── Phase 5 — equity subgroup min/max (as % of total_equity_for_subgroups) ────
# Source: references/long-term-goals.md lines 200-219
# Columns: us_equities, low_beta_equities, medium_beta_equities, high_beta_equities,
# sector_equities, value_equities
_P5_ROWS: list[tuple[float, tuple[int, int], tuple[int, int], tuple[int, int], tuple[int, int], tuple[int, int], tuple[int, int]]] = [
    (10.0, (20, 40), (0, 20),  (30, 50), (10, 30), (0, 20), (0, 30)),
    (9.5,  (20, 40), (0, 20),  (30, 50), (10, 30), (0, 20), (0, 30)),
    (9.0,  (20, 40), (0, 25),  (25, 50), (10, 30), (0, 20), (0, 30)),
    (8.5,  (20, 40), (0, 30),  (25, 45), (10, 30), (0, 20), (0, 30)),
    (8.0,  (20, 40), (10, 30), (20, 40), (5, 25),  (0, 20), (0, 30)),
    (7.5,  (20, 40), (15, 35), (20, 40), (5, 25),  (0, 20), (0, 30)),
    (7.0,  (20, 40), (20, 40), (20, 40), (5, 25),  (0, 20), (0, 30)),
    (6.5,  (20, 40), (25, 45), (20, 40), (5, 20),  (0, 20), (0, 30)),
    (6.0,  (20, 40), (30, 50), (15, 35), (5, 20),  (0, 20), (0, 30)),
    (5.5,  (20, 40), (30, 55), (10, 35), (5, 20),  (0, 20), (0, 30)),
    (5.0,  (20, 40), (35, 55), (10, 30), (0, 20),  (0, 0),  (0, 30)),
    (4.5,  (20, 40), (40, 60), (5, 25),  (0, 20),  (0, 0),  (0, 30)),
    (4.0,  (20, 40), (45, 65), (5, 25),  (0, 0),   (0, 0),  (0, 30)),
    (3.5,  (20, 40), (50, 70), (0, 20),  (0, 0),   (0, 0),  (0, 30)),
    (3.0,  (20, 40), (55, 75), (0, 20),  (0, 0),   (0, 0),  (0, 30)),
    (2.5,  (20, 40), (60, 80), (0, 20),  (0, 0),   (0, 0),  (0, 30)),
    (2.0,  (20, 40), (60, 80), (0, 0),   (0, 0),   (0, 0),  (0, 30)),
    (1.5,  (20, 40), (60, 80), (0, 0),   (0, 0),   (0, 0),  (0, 30)),
    (1.0,  (20, 40), (60, 80), (0, 0),   (0, 0),   (0, 0),  (0, 30)),
]

PHASE5_EQUITY_SUBGROUP_BOUNDS: dict[float, dict[str, tuple[int, int]]] = {
    score: {
        "us_equities": us,
        "low_beta_equities": lb,
        "medium_beta_equities": mb,
        "high_beta_equities": hb,
        "sector_equities": sec,
        "value_equities": val,
    }
    for score, us, lb, mb, hb, sec, val in _P5_ROWS
}


# ── Medium-term horizon × risk bucket → (equity_pct, debt_pct) ─────────────────
# Source: references/medium-term-goals.md lines 34-38
MEDIUM_TERM_SPLIT: dict[tuple[int, str], tuple[int, int]] = {
    (5, "Low"):    (50, 50),
    (5, "Medium"): (70, 30),
    (5, "High"):   (80, 20),
    (4, "Low"):    (35, 65),
    (4, "Medium"): (50, 50),
    (4, "High"):   (65, 35),
    (3, "Low"):    (0, 100),
    (3, "Medium"): (0, 100),
    (3, "High"):   (0, 100),
}


# ── Subgroup → asset class roll-up ────────────────────────────────────────────
# Used by step6 guardrail messaging and any future internal subgroup→class
# roll-up. Specific fund/ISIN suggestions live elsewhere (Rebalancing's
# Prozpr_fund_ranking.csv); allocation only commits to the three asset classes.
SUBGROUP_TO_ASSET_CLASS: dict[str, str] = {
    "low_beta_equities": "equity",
    "medium_beta_equities": "equity",
    "high_beta_equities": "equity",
    "value_equities": "equity",
    "dividend_equities": "equity",
    "tax_efficient_equities": "equity",
    "sector_equities": "equity",
    "us_equities": "equity",
    "multi_asset": "equity",
    "debt_subgroup": "debt",
    "short_debt": "debt",
    "arbitrage": "debt",
    "arbitrage_plus_income": "debt",
    "gold_commodities": "others",
    "silver_commodities": "others",
    "china_equities": "others",
    "others_fofs": "others",
    "others": "others",
}


# ── Policy constants (tuneable without touching step code) ────────────────────

# Default market-commentary scores (1-10 scale) when the caller doesn't supply
# a view. Grouped by asset-class level vs equity-subgroup level so each can be
# tuned independently when the house view shifts.
DEFAULT_MARKET_COMMENTARY_SCORES: dict[str, dict[str, float]] = {
    "asset_class": {
        "equities": 5.0,
        "debt": 5.0,
        "others": 5.0,
    },
    "subgroup": {
        "low_beta_equities": 5.0,
        "value_equities": 5.0,
        "dividend_equities": 5.0,
        "medium_beta_equities": 5.0,
        "high_beta_equities": 5.0,
        "sector_equities": 5.0,
        "us_equities": 5.0,
    },
}

# Default composition of the multi-asset fund (equity, debt, others), summing to 100.
DEFAULT_MULTI_ASSET_COMPOSITION_PCTS: tuple[float, float, float] = (65.0, 25.0, 10.0)

# Emergency fund months by income source.
EMERGENCY_FUND_MONTHS: dict[str, int] = {
    "standard": 3,
    "primary_income_from_portfolio": 6,
}

# Bucket boundaries (in months) used when classifying goals.
# short-term:   months <  MEDIUM_TERM_BOUNDARY_MONTHS
# medium-term:  MEDIUM_TERM_BOUNDARY_MONTHS <= months <= LONG_TERM_BOUNDARY_MONTHS
# long-term:    months >  LONG_TERM_BOUNDARY_MONTHS
MEDIUM_TERM_BOUNDARY_MONTHS: int = 24
LONG_TERM_BOUNDARY_MONTHS: int = 60
# Medium-term horizon (years) clamp used to pick a row from MEDIUM_TERM_SPLIT.
MEDIUM_TERM_HORIZON_MIN: int = 3
MEDIUM_TERM_HORIZON_MAX: int = 5

# Tax-rate thresholds (%) for routing debt allocations to arbitrage vs pure debt.
# `>=` comparison: at the threshold itself, allocation routes to arbitrage.
# Short-term uses a higher bar (30%) since tax efficiency matters less for <2y
# holdings; medium/long-term uses 15% since arbitrage gains get equity taxation.
TAX_RATE_SHORT_TERM_ARBITRAGE_THRESHOLD: float = 30.0
TAX_RATE_MEDIUM_LONG_ARBITRAGE_THRESHOLD: float = 15.0

# Long-term equity subgroups smaller than this share of total long-term equity
# are dropped and their amount is redistributed proportionally across the
# remaining equity subgroups (ELSS and multi-asset are excluded from both the
# filter and the redistribution).
MIN_EQUITY_SUBGROUP_SHARE_PCT: float = 8.0

# Phase 5 internal: within the equity-subgroups split itself, any subgroup whose
# share falls below this percent is rolled into the others (applied up to twice).
PHASE5_MIN_SUBGROUP_SHARE_PCT: int = 2

# Medium-term risk-bucket thresholds (on the effective_risk_score 1-10 scale).
# score < LOW_MAX_EXCLUSIVE → Low
# LOW_MAX_EXCLUSIVE <= score <= MEDIUM_MAX → Medium
# score > MEDIUM_MAX → High
MEDIUM_TERM_RISK_LOW_MAX_EXCLUSIVE: float = 4.0
MEDIUM_TERM_RISK_MEDIUM_MAX: float = 7.0


# Phase 1 others-gate: at high risk with a tepid view on others, zero it out.
OTHERS_GATE_SCORE_THRESHOLD: float = 8.0
OTHERS_GATE_MARKET_VIEW_THRESHOLD: float = 6.0

# Phase 1 intergenerational-transfer override (applied when client is elderly
# and at least one long-term goal is tagged as intergen transfer).
INTERGEN_MIN_AGE: int = 60
INTERGEN_SCORE_BOOST: float = 2.0
INTERGEN_SCORE_CAP: float = 9.0

# Phase 3 ELSS: section 80C annual limit (₹).
SECTION_80C_LIMIT: int = 150000

# Phase 4 multi-asset equity cap: the fund's equity slice may consume at most
# this fraction of the residual equity corpus.
MULTI_ASSET_EQUITY_CAP_PCT: float = 0.50

# Phase 5 market-view gates: subgroups are dropped when the view <= threshold.
PHASE5_MARKET_VIEW_GATES: dict[str, float] = {
    "value_equities": 7.0,
    "sector_equities": 7.0,
}

# Step 6 guardrail: rounding tolerance (in percentage points) for Phase 5 shares.
PHASE5_SHARE_TOLERANCE_PP: int = 1

# Step 4 invariant tolerance (₹): asset-class reconciliation gaps below this are
# absorbed silently since they stem from independent round-to-100 operations.
ASSET_CLASS_RECONCILIATION_TOLERANCE: int = 500

# Proportional-redistribution epsilon: values within this margin of their min/max
# are considered already-clamped when rebalancing the remainder.
CLAMP_EPSILON: float = 1e-9

# Maximum iterations of the clamp/redistribute loop before it gives up and
# returns whatever it has; 8 is comfortably past empirical convergence (~3-4).
CLAMP_MAX_ITER: int = 8

# Market-commentary scale calibration. Views run 1–10; a view of MARKET_VIEW_CENTER
# is neutral, and the range [CENTER - HALF_RANGE, CENTER + HALF_RANGE] spans the
# normalized [-1, +1] tilt used to skew allocations off their midpoint.
MARKET_VIEW_CENTER: float = 5.0
MARKET_VIEW_HALF_RANGE: float = 5.0

# Long-term equity subgroups split by Phase 5 (ELSS and multi-asset live
# outside this list). Shared between step 4 (allocation) and step 6 (guardrails).
EQUITY_SUBGROUPS: tuple[str, ...] = (
    "us_equities",
    "low_beta_equities",
    "medium_beta_equities",
    "high_beta_equities",
    "sector_equities",
    "value_equities",
)

# Every subgroup step 4 may write: ELSS + multi-asset + long-term equity split
# + one debt bucket + gold. (short_debt and arbitrage are short-term-only.)
STEP4_SUBGROUPS: tuple[str, ...] = (
    "tax_efficient_equities",
    "multi_asset",
    *EQUITY_SUBGROUPS,
    "debt_subgroup",
    "arbitrage_plus_income",
    "gold_commodities",
)

# LLM settings for the rationale call (Step 7).
LLM_MODEL_ID: str = "claude-haiku-4-5-20251001"
LLM_MAX_TOKENS: int = 1500
LLM_MAX_RETRIES: int = 2
