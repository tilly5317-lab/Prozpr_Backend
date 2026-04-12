from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Raw macro data (populated by Claude Haiku extraction from search snippets)
# ---------------------------------------------------------------------------


class MacroSnapshot(BaseModel):
    """Structured macro-indicator snapshot for Indian markets."""

    # RBI / Monetary policy
    repo_rate_pct: Optional[float] = None
    rbi_stance: Optional[str] = None  # "hawkish" | "neutral" | "accommodative"

    # Inflation
    cpi_yoy_pct: Optional[float] = None

    # Equity valuations
    nifty50_pe: Optional[float] = None
    nifty_midcap150_pe: Optional[float] = None
    nifty_smallcap250_pe: Optional[float] = None

    # Debt market
    gsec_10yr_yield_pct: Optional[float] = None
    sbi_fd_1yr_rate_pct: Optional[float] = None

    # Gold
    gold_price_inr_per_10g: Optional[float] = None
    gold_price_usd_per_oz: Optional[float] = None

    # Global macro
    fed_funds_rate_pct: Optional[float] = None
    fii_net_flows_cr_inr: Optional[float] = None  # latest month, crore INR

    # Oil
    brent_crude_usd: Optional[float] = None

    # FX
    usd_inr_rate: Optional[float] = None

    # Metadata — indicators where extraction returned null
    data_gaps: List[str] = Field(default_factory=list)

    # Generated document — populated after document generation step; not persisted to cache
    document_md: Optional[str] = None
