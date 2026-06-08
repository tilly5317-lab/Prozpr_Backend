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
SHORT_DEBT_FUND_CAP_PCT: float = float(os.getenv("REBAL_SHORT_DEBT_FUND_CAP_PCT", "30.0"))
ARBITRAGE_FUND_CAP_PCT: float = float(os.getenv("REBAL_ARBITRAGE_FUND_CAP_PCT", "30.0"))
REBALANCE_MIN_CHANGE_PCT: float = float(os.getenv("REBAL_MIN_CHANGE_PCT", "0.10"))
EXIT_FLOOR_RATING: int = int(os.getenv("REBAL_EXIT_FLOOR_RATING", "5"))

# Sentinel rank on `FundRowInput` marking explicitly-bad funds the upstream
# input builder wants force-exited. Rows with `rank == FORCE_EXIT_RANK`
# trigger `exit_flag = True` in step2 and full liquidation in step4
# regardless of tax cost. Same constant duplicated in the app-side
# `fund_rank.FORCE_EXIT_RANK` (CSV loader); keep both in sync.
FORCE_EXIT_RANK: int = 9999


# ── Bucket C — tax limits ─────────────────────────────────────────────────────

LTCG_ANNUAL_EXEMPTION_INR: Decimal = Decimal(os.getenv("REBAL_LTCG_EXEMPTION_INR", "125000"))
STCG_RATE_EQUITY_PCT: float = float(os.getenv("REBAL_STCG_RATE_EQUITY", "20.0"))
LTCG_RATE_EQUITY_PCT: float = float(os.getenv("REBAL_LTCG_RATE_EQUITY", "12.5"))
ST_THRESHOLD_MONTHS_EQUITY: int = int(os.getenv("REBAL_ST_THRESHOLD_EQUITY", "12"))
ST_THRESHOLD_MONTHS_DEBT: int = int(os.getenv("REBAL_ST_THRESHOLD_DEBT", "24"))


# ── Engine version ────────────────────────────────────────────────────────────
# Bump on logic changes that alter output for the same inputs.
ENGINE_VERSION: str = "1.0.0"
