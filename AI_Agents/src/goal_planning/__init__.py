"""Goal Planning AI module — public API.

Bridge code imports from here only. Internal types (RunContext, MortgageSchedule, etc.)
live in engine/_types.py and are NOT exported.
"""
from .engine import compute_full_projection, validate_input_only, ENGINE_VERSION
from .agent import goal_planning_graph, run_goal_planning_agent
from .models import (
    # Inputs
    GoalPlanningInput, Assumptions, ClientProfile, RetirementInput,
    CurrentProperty, GoalProperty, CustomGoal, OneOffEvent,
    # Outputs
    GoalPlanningOutput, GoalPlanningResponse,
    HeadlineStatus, RetirementSnapshot, FundFlowSummary,
    GoalFundingStatus, OneOffFundingStatus,
    AnnualCashflowRow, MonthlyCashflowRow, MonthlyNFARow,
    MortgageAmortization, MortgageAmortizationRow,
    ValidationIssue,
    # Agent types
    OverrideSpec, NumericOverride, RateOverride, PerGoalRateOverride, PropertyFieldOverride,
    GoalMutation, LeverAction, Lever,
    ExtractedFinancialEvent, ExtractedGoal, ExtractedProperty,
    ExtractedCashflow, ExtractedMutation, ExtractionError,
    # Enums
    GoalType,
)

__all__ = [
    "compute_full_projection", "validate_input_only", "ENGINE_VERSION",
    "goal_planning_graph", "run_goal_planning_agent",
    "GoalPlanningInput", "Assumptions", "ClientProfile", "RetirementInput",
    "CurrentProperty", "GoalProperty", "CustomGoal", "OneOffEvent",
    "GoalPlanningOutput", "GoalPlanningResponse",
    "HeadlineStatus", "RetirementSnapshot", "FundFlowSummary",
    "GoalFundingStatus", "OneOffFundingStatus",
    "AnnualCashflowRow", "MonthlyCashflowRow", "MonthlyNFARow",
    "MortgageAmortization", "MortgageAmortizationRow",
    "ValidationIssue",
    "OverrideSpec", "NumericOverride", "RateOverride",
    "PerGoalRateOverride", "PropertyFieldOverride",
    "GoalMutation", "LeverAction", "Lever",
    "ExtractedFinancialEvent", "ExtractedGoal", "ExtractedProperty",
    "ExtractedCashflow", "ExtractedMutation", "ExtractionError",
    "GoalType",
]
