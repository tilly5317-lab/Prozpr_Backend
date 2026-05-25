"""practical_asset_allocation pipeline — see module __init__ docstring."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from asset_allocation_pydantic.models import AllocationInput


class InfeasibleGoalError(ValueError):
    """Raised when the input corpus cannot satisfy structural constraints
    (e.g. ELSS holdings exceed total corpus)."""


class PracticalAllocationInput(AllocationInput):
    """Extends AllocationInput with four holdings-aware corpus scalars.

    Implicit corpus accounting (not separate inputs):
      cash               = total_corpus - mf_corpus - non_mf_equity_corpus
      mf_non_elss        = mf_corpus - elss_corpus
      rebalancing_corpus = total_corpus - elss_corpus
    """

    mf_corpus: float = Field(..., ge=0)
    """Total MF holdings INCLUDING ELSS."""

    non_mf_equity_corpus: float = Field(default=0.0, ge=0)
    """Direct stocks + PMS — non-MF equity, treated separately because the
    rebalancing engine can't trade them per-fund."""

    elss_corpus: float = Field(default=0.0, ge=0)
    """ELSS MF holdings (subset of mf_corpus). Locked under 3-year SEBI
    lock-in — surfaced as a frozen long-term row."""

    max_non_mf_equity_pct_client_input: Optional[float] = Field(default=None)
    """Advisor override for the NFA-banded non-MF equity cap (Option A)."""


# Stubs filled in by later tasks. Keep the import surface stable.
class CorpusBreakdown(BaseModel):
    """Filled in by Task 3."""


class PracticalAllocationOutput(BaseModel):
    """Filled in by Task 3."""


def run_practical_allocation(inp):  # type: ignore[no-untyped-def]
    raise NotImplementedError(
        "run_practical_allocation: implementation lands in Tasks 4-12."
    )
