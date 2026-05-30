"""Pydantic schemas for cashflow_statement persistence."""

from app.domains.cashflow.schemas.assumptions import (
    CashflowAssumptionsCreate,
    CashflowAssumptionsResponse,
    CashflowAssumptionsUpdate,
)
from app.domains.cashflow.schemas.enums import (
    CashflowGoalType,
    DetailLevel,
    InvestmentSource,
    OneOffDirection,
)
from app.domains.cashflow.schemas.goals import (
    CashflowGoalCreate,
    CashflowGoalResponse,
    CashflowGoalUpdate,
)
from app.domains.cashflow.schemas.input import CashflowClientProfile, CashflowPlanningInput
from app.domains.cashflow.schemas.one_off import (
    CashflowOneOffEventCreate,
    CashflowOneOffEventResponse,
    CashflowOneOffEventUpdate,
)
from app.domains.cashflow.schemas.outputs import (
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
