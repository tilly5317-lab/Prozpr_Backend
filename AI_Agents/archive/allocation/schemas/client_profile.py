from typing import Literal
from pydantic import BaseModel


class ClientProfile(BaseModel):
    age: int
    risk_profile: Literal["conservative", "moderate", "aggressive"]
    investment_horizon_years: int
    goals: list[str]
    annual_income_lakhs: float
    existing_liabilities_lakhs: float
