from typing import Any, Dict, Literal, Optional
from pydantic import BaseModel, Field

OccupationType = Literal[
    "public_sector",
    "private_sector",
    "family_business",
    "commission_based",
    "freelancer_gig",
    "retired_homemaker_student",
]


class RiskProfileInput(BaseModel):
    age: int
    occupation_type: OccupationType
    annual_income: float
    annual_expense: float
    financial_assets: float
    liabilities_excluding_mortgage: float
    annual_mortgage_payment: float
    properties_owned: int  # 0, 1, or >1
    risk_willingness: float = Field(..., ge=1, le=10)


class RiskProfileOutput(BaseModel):
    step_name: str = "risk_profile"
    inputs: Dict[str, Any]
    calculations: Dict[str, Any]
    output: Dict[str, Any]  # effective_risk_score + risk_summary
