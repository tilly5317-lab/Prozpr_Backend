from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field

from goal_based_allocation_pydantic.models import GoalAllocationOutput


# ── Inputs ───────────────────────────────────────────────────────────────────


class ActualHolding(BaseModel):
    scheme_code: str
    scheme_name: str
    asset_class: str
    asset_subgroup: str
    isin: str
    current_value: float = Field(..., ge=0)
    invested_amount: float = Field(..., ge=0)


class DriftInput(BaseModel):
    ideal_allocation: GoalAllocationOutput
    actual_holdings: List[ActualHolding]


# ── Outputs ──────────────────────────────────────────────────────────────────


class FundDrift(BaseModel):
    scheme_code: str
    scheme_name: str
    isin: str
    asset_class: str
    asset_subgroup: str
    display_name: str
    is_recommended: bool
    ideal_amount: float = Field(default=0.0)
    actual_amount: float = Field(default=0.0)
    drift_amount: float = 0.0
    drift_pct: float = 0.0


class SubgroupDrift(BaseModel):
    subgroup: str
    display_name: str
    asset_class: str
    ideal_amount: float = Field(default=0.0)
    actual_amount: float = Field(default=0.0)
    drift_amount: float = 0.0
    drift_pct: float = 0.0
    funds: List[FundDrift] = Field(default_factory=list)


class AssetClassDrift(BaseModel):
    asset_class: str
    ideal_amount: float = Field(default=0.0)
    ideal_pct: float = 0.0
    actual_amount: float = Field(default=0.0)
    actual_pct: float = 0.0
    drift_amount: float = 0.0
    drift_pct: float = 0.0
    subgroups: List[SubgroupDrift] = Field(default_factory=list)


class DriftOutput(BaseModel):
    total_ideal_value: float
    total_actual_value: float
    asset_classes: List[AssetClassDrift]
