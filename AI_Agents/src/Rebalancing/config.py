"""Module-level configuration knobs for the rebalancing engine.

Values are env-overrideable for ops tuning without code changes. Buckets A
(caps & thresholds) and C (tax limits) per `Reference_docs/input_parameter_spec.md`;
bucket D (per-request capital-gains state) lives on the request object.
"""

from __future__ import annotations

import os
from decimal import Decimal


# ── Bucket A — caps & thresholds ─────────────────────────────────────────────

MULTI_FUND_CAP_PCT: float = float(os.getenv("REBAL_MULTI_FUND_CAP_PCT", "20.0"))
OTHERS_FUND_CAP_PCT: float = float(os.getenv("REBAL_OTHERS_FUND_CAP_PCT", "10.0"))
# Per Excel R247: `short_debt` carries a higher per-fund cap than the
# generic 10% because the universe of high-quality short-duration debt
# funds is small and concentration risk is correspondingly lower.
SHORT_DEBT_FUND_CAP_PCT: float = float(
    os.getenv("REBAL_SHORT_DEBT_FUND_CAP_PCT", "30.0")
)
ARBITRAGE_FUND_CAP_PCT: float = float(os.getenv("REBAL_ARBITRAGE_FUND_CAP_PCT", "30.0"))
# Rupee floor on the per-fund cap (amendment 2026-07-06): step 1 caps each
# fund at max(cap_pct × corpus, this floor), so a small portfolio is never
# force-split into sub-₹1L fund positions — and, symmetrically, a small
# over-cap holding is not trimmed just to satisfy a tiny percentage cap.
# For corpora ≥ ₹10L at the 10% default the floor never binds.
FUND_CAP_FLOOR_INR: Decimal = Decimal(os.getenv("REBAL_FUND_CAP_FLOOR_INR", "100000"))
REBALANCE_MIN_CHANGE_PCT: float = float(os.getenv("REBAL_MIN_CHANGE_PCT", "0.10"))
# Subgroup fund-count selection (spec 2026-09-20). Below this line one fund takes
# an asset_subgroup's deployable money; at or above it, two share it. Replaces
# the old rule where the fund count was an emergent property of the per-fund cap.
#
# Measured on `total_corpus - non_mf_equity_corpus` — NOT `mf_corpus`, which is
# an independent input and excludes cash (practical pipeline: `cash =
# total_corpus - mf_corpus - non_mf_equity_corpus`). The two agree only when the
# customer holds no cash, which is true of every sim profile.
#
# One knob, not three: where the line sits is ops tuning, but the counts 1 and 2
# are the rule itself and are hardcoded in `pipeline._funds_per_subgroup`.
SUBGROUP_FUND_COUNT_THRESHOLD_INR: Decimal = Decimal(
    os.getenv("REBAL_SUBGROUP_FUND_COUNT_THRESHOLD_INR", "5000000")
)
# Step 2b: cancel matched debt sell/buy intents so one debt fund is never sold
# to buy another (design note 2026-07-18). Kill-switch for ops, and the seam
# for A/B-ing the change against the simulation harnesses.
DEBT_SWITCH_NETTING_ENABLED: bool = os.getenv(
    "REBAL_DEBT_SWITCH_NETTING", "1"
).strip().lower() not in ("", "0", "false", "no", "off")
# How step2b redistributes the buy side once a sell is cancelled.
#   "pro_rata"  — shrink each surviving buy in proportion to its own demand.
#   "cap_spill" — recompute the subgroup's remaining budget and allocate it
#                 down the rank ladder under the per-fund cap, exactly as step1
#                 does. Best-ranked fund first; a subgroup already at target
#                 buys nothing.
DEBT_NETTING_MODE: str = os.getenv(
    "REBAL_DEBT_NETTING_MODE", "cap_spill"
).strip().lower()
# Holdings-aware targets (design note 2026-07-19). A held recommended fund keeps
# its holding as its target — instead of the 0 that `input_builder.py:272`
# assigns to every rank >= 2 — provided the gap to the best-ranked fund in its
# subgroup is UNDER `RANK_PROTECT_BAND`. EXCLUSIVE: gap 4 protects, gap 5 sells.
#
# Measured: 69.4% of real held rows are rank >= 2, so this is the dominant
# pattern in production, not an edge case.
#
# The band is ABSOLUTE, not a fraction of ladder depth (product decision). Note
# it therefore loosens on its own as ranking ladders deepen.
RANK_PROTECT_BAND: int = int(os.getenv("REBAL_RANK_PROTECT_BAND", "5"))
HOLDINGS_AWARE_TARGETS_ENABLED: bool = os.getenv(
    "REBAL_HOLDINGS_AWARE_TARGETS", "1"
).strip().lower() not in ("", "0", "false", "no", "off")
EXIT_FLOOR_RATING: int = int(os.getenv("REBAL_EXIT_FLOOR_RATING", "5"))
# Additional-investment SIP per-fund cap floor (rupees): the SIP selector caps
# each buy at max(cap_pct × monthly amount, this floor), so a small SIP stays
# concentrated in few funds instead of fragmenting down the ranking
# (spec 2026-07-06). Lives here with the other cap thresholds; consumed by the
# additional_investment input builder, not by the rebalancing engine itself.
AINV_SIP_FUND_CAP_FLOOR_INR: float = float(
    os.getenv("AINV_SIP_FUND_CAP_FLOOR_INR", "10000")
)
# Same floored-cap rule for lumpsum deployments (both deficit-fill and the
# no-holdings legacy path): cap = max(cap_pct × lumpsum amount, this floor).
AINV_LUMPSUM_FUND_CAP_FLOOR_INR: float = float(
    os.getenv("AINV_LUMPSUM_FUND_CAP_FLOOR_INR", "40000")
)

# Sentinel rank on `FundRowInput` marking explicitly-bad funds the upstream
# input builder wants force-exited. Rows with `rank == FORCE_EXIT_RANK`
# trigger `exit_flag = True` in step2 and full liquidation in step4
# regardless of tax cost. Same constant duplicated in the app-side
# `fund_rank.FORCE_EXIT_RANK` (CSV loader); keep both in sync.
FORCE_EXIT_RANK: int = 9999


# ── Bucket C — tax limits ─────────────────────────────────────────────────────

LTCG_ANNUAL_EXEMPTION_INR: Decimal = Decimal(
    os.getenv("REBAL_LTCG_EXEMPTION_INR", "125000")
)
STCG_RATE_EQUITY_PCT: float = float(os.getenv("REBAL_STCG_RATE_EQUITY", "20.0"))
LTCG_RATE_EQUITY_PCT: float = float(os.getenv("REBAL_LTCG_RATE_EQUITY", "12.5"))
ST_THRESHOLD_MONTHS_EQUITY: int = int(os.getenv("REBAL_ST_THRESHOLD_EQUITY", "12"))
ST_THRESHOLD_MONTHS_DEBT: int = int(os.getenv("REBAL_ST_THRESHOLD_DEBT", "24"))


# ── Engine version ────────────────────────────────────────────────────────────
# Bump on logic changes that alter output for the same inputs.
# 1.1.0: per-fund cap floored at FUND_CAP_FLOOR_INR (amendment 2026-07-06).
# 1.2.0: step2b suppresses debt-for-debt switching (design note 2026-07-18);
#        surviving buy demand re-spills down the rank ladder under the
#        per-fund cap (cap_spill), matching step1 rather than pro-rating.
# 1.3.0: holdings-aware targets (design note 2026-07-19). A held recommended
#        fund inside RANK_PROTECT_BAND reserves what it holds and only the
#        residual is deployed as fresh money, so a rank-2 holding is no longer
#        liquidated to fund a rank-1 buy; the per-fund cap no longer forces a
#        sell, only bounds deployment.
# 1.6.0: human_override step in PAA (S1) — output-altering when a preference exists
# 1.7.0: legacy per-turn sleeve tilt deleted (S2-R3) — preferences ride the PAA
#        human_override step only.
# 1.10.0: subcategory full distribution (spec 2026-09-15). A subgroup absent
#         from the practical allocation means a ZERO target, not "keep whatever
#         the input builder seeded from the IDEAL output" (§2); a stated
#         preference suspends the emergency / short / medium carve-outs (§3);
#         `arbitrage` becomes a long-term subgroup, reachable only by asking
#         for it (§4); the long-term debt residual splits pro-rata across the
#         rows the customer named instead of landing in one (§7).
# 1.11.0: subgroup fund-count selection (spec 2026-09-20). A subgroup's
#         deployable money is split across its top 1 or 2 ranked funds — 1 below
#         ₹50L net of non-MF equity, 2 at or above — rather than cascading down
#         the rank ladder under the per-fund cap. The per-fund cap no longer
#         bounds deployment anywhere in the engine.
ENGINE_VERSION: str = "1.11.0"
