"""Goal Planning AI module — public API.

Bridge code imports from here only. Internal types (RunContext, MortgageSchedule, etc.)
live in engine/_types.py and are NOT exported.
"""
from .engine import compute_full_projection, validate_input_only, ENGINE_VERSION
from .agent import cashflow_statement_graph, run_cashflow_statement
from .models import (
    # Inputs
    GoalPlanningInput, Assumptions, ClientProfile, RetirementInput,
    CurrentProperty, GoalProperty, CustomGoal, OneOffEvent,
    # Outputs
    GoalPlanningOutput, GoalPlanningResponse,
    HeadlineStatus, RetirementSnapshot, FundFlowSummary,
    GoalFundingStatus, OneOffFundingStatus,
    AnnualCashflowRow, MonthlyCashflowRow,
    ValidationIssue,
    # v2 types
    GoalPlanningRequest, GoalPlanningSnapshot,
    GoalPropertyDetail,
    TurnAction,
    # Agent types
    OverrideSpec, NumericOverride, RateOverride, PerGoalRateOverride, PropertyFieldOverride,
    GoalMutation, LeverAction, Lever,
    ExtractedFinancialEvent, ExtractedGoal, ExtractedProperty,
    ExtractedCashflow, ExtractedMutation, ExtractionError,
    # Summary
    PlanSummary, GoalBullet,
    # Enums
    GoalType,
)
from .summarizer import summarize_plan

__all__ = [
    "compute_full_projection", "validate_input_only", "ENGINE_VERSION",
    "cashflow_statement_graph", "run_cashflow_statement",
    "GoalPlanningInput", "Assumptions", "ClientProfile", "RetirementInput",
    "CurrentProperty", "GoalProperty", "CustomGoal", "OneOffEvent",
    "GoalPlanningOutput", "GoalPlanningResponse",
    "HeadlineStatus", "RetirementSnapshot", "FundFlowSummary",
    "GoalFundingStatus", "OneOffFundingStatus",
    "AnnualCashflowRow", "MonthlyCashflowRow",
    "ValidationIssue",
    "GoalPlanningRequest", "GoalPlanningSnapshot",
    "GoalPropertyDetail",
    "TurnAction",
    "OverrideSpec", "NumericOverride", "RateOverride",
    "PerGoalRateOverride", "PropertyFieldOverride",
    "GoalMutation", "LeverAction", "Lever",
    "ExtractedFinancialEvent", "ExtractedGoal", "ExtractedProperty",
    "ExtractedCashflow", "ExtractedMutation", "ExtractionError",
    "PlanSummary", "GoalBullet", "summarize_plan",
    "GoalType",
]
