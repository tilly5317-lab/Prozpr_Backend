"""Public facade for the additional-investment engine: the run entry point + its pydantic I/O models."""

from .models import (
    Cadence,
    TargetBucket,
    SubgroupBucketAmounts,
    RankedFund,
    AdditionalInvestmentInput,
    SubgroupTarget,
    FundBuy,
    AdditionalInvestmentOutput,
)
from .pipeline import run_additional_investment

__all__ = [
    "run_additional_investment",
    "AdditionalInvestmentInput",
    "AdditionalInvestmentOutput",
    "SubgroupBucketAmounts",
    "RankedFund",
    "SubgroupTarget",
    "FundBuy",
    "Cadence",
    "TargetBucket",
]
