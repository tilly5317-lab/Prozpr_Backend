"""Pydantic schemas for cashflow_statement persistence."""

from app.schemas.cashflow.assumptions import (
    CashflowAssumptionsCreate,
    CashflowAssumptionsResponse,
    CashflowAssumptionsUpdate,
)
from app.schemas.cashflow.enums import (
    CashflowGoalType,
    DetailLevel,
    InvestmentSource,
    OneOffDirection,
)
from app.schemas.cashflow.goals import (
    CashflowGoalCreate,
    CashflowGoalResponse,
    CashflowGoalUpdate,
)
from app.schemas.cashflow.input import CashflowClientProfile, CashflowPlanningInput
from app.schemas.cashflow.one_off import (
    CashflowOneOffEventCreate,
    CashflowOneOffEventResponse,
    CashflowOneOffEventUpdate,
)
from app.schemas.cashflow.outputs import (
    AnnualCashflowRowSchema,
    CashflowPlanRunCreate,
    CashflowPlanRunDetailResponse,
    CashflowPlanRunResponse,
    FundFlowSummarySchema,
    HeadlineStatusSchema,
    MonthlyCashflowRowSchema,
    PlanSummarySchema,
)
__all__ = [
    "AnnualCashflowRowSchema",
    "CashflowAssumptionsCreate",
    "CashflowAssumptionsResponse",
    "CashflowAssumptionsUpdate",
    "CashflowClientProfile",
    "CashflowGoalCreate",
    "CashflowGoalResponse",
    "CashflowGoalType",
    "CashflowGoalUpdate",
    "CashflowOneOffEventCreate",
    "CashflowOneOffEventResponse",
    "CashflowOneOffEventUpdate",
    "CashflowPlanRunCreate",
    "CashflowPlanRunDetailResponse",
    "CashflowPlanRunResponse",
    "CashflowPlanningInput",
    "DetailLevel",
    "FundFlowSummarySchema",
    "HeadlineStatusSchema",
    "InvestmentSource",
    "MonthlyCashflowRowSchema",
    "OneOffDirection",
    "PlanSummarySchema",
]
