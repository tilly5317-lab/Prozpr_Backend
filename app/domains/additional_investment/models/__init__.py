"""Additional-investment engine output — runs plus per-subgroup targets and BUY rows.

Each ``AdditionalInvestmentRun`` references the persisted PRACTICAL allocation
run (``PracticalAssetAllocationRun`` — table ``practical_asset_allocation_runs``)
it is derived from, which supplies the per-bucket subgroup amounts the engine
splits the deploy amount across.
"""

from app.domains.additional_investment.models.additional_investment_run import (
    AdditionalInvestmentBuy,
    AdditionalInvestmentRun,
    AdditionalInvestmentTarget,
    TargetBucket,
    Cadence,
)

__all__ = [
    "AdditionalInvestmentBuy",
    "AdditionalInvestmentRun",
    "AdditionalInvestmentTarget",
    "TargetBucket",
    "Cadence",
]
