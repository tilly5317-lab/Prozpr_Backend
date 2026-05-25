"""Cashflow-statement persistence package.

User-scoped inputs (`CashflowInputAssumption`, `CashflowInputOneOffEvent`) plus
the per-run output hierarchy (`CashflowPlanRun` → annual rows, monthly rows,
headline, fund flow summary, plan summary). Mirrors the public types in
`AI_Agents/src/cashflow_statement/models.py`; see `viewer_db_schema.md` for the
column-level contract.
"""


from app.models.cashflow.annual_row import CashflowAnnualRow
from app.models.cashflow.assumption import CashflowInputAssumption
from app.models.cashflow.enums import (
    DetailLevel,
    GoalTypeCashflow,
    InvestmentSource,
    OneOffDirection,
)
from app.models.cashflow.fund_flow_summary import CashflowFundFlowSummary
from app.models.cashflow.headline import CashflowHeadline
from app.models.cashflow.monthly_row import CashflowMonthlyRow
from app.models.cashflow.one_off_event import CashflowInputOneOffEvent
from app.models.cashflow.plan_run import CashflowPlanRun
from app.models.cashflow.plan_summary import CashflowPlanSummary

__all__ = [
    "CashflowAnnualRow",
    "CashflowFundFlowSummary",
    "CashflowHeadline",
    "CashflowInputAssumption",
    "CashflowInputOneOffEvent",
    "CashflowMonthlyRow",
    "CashflowPlanRun",
    "CashflowPlanSummary",
    "DetailLevel",
    "GoalTypeCashflow",
    "InvestmentSource",
    "OneOffDirection",
]
