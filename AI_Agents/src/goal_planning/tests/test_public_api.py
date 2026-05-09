def test_public_api_exports():
    import goal_planning as gp

    # Engine
    assert hasattr(gp, "compute_full_projection")
    assert hasattr(gp, "validate_input_only")
    assert hasattr(gp, "ENGINE_VERSION")

    # Agent
    assert hasattr(gp, "goal_planning_graph")
    assert hasattr(gp, "run_goal_planning")

    # Inputs
    for name in ["GoalPlanningInput", "Assumptions", "ClientProfile", "RetirementInput",
                 "CurrentProperty", "GoalProperty", "CustomGoal", "OneOffEvent"]:
        assert hasattr(gp, name), f"missing {name}"

    # Outputs
    for name in ["GoalPlanningOutput", "GoalPlanningResponse",
                 "HeadlineStatus", "RetirementSnapshot", "FundFlowSummary",
                 "GoalFundingStatus", "OneOffFundingStatus",
                 "AnnualCashflowRow", "MonthlyCashflowRow", "MonthlyNFARow",
                 "MortgageAmortization", "MortgageAmortizationRow",
                 "ValidationIssue"]:
        assert hasattr(gp, name), f"missing {name}"

    # Agent types
    for name in ["OverrideSpec", "NumericOverride", "RateOverride",
                 "PerGoalRateOverride", "PropertyFieldOverride",
                 "GoalMutation", "LeverAction", "Lever",
                 "ExtractedFinancialEvent", "ExtractedGoal", "ExtractedProperty",
                 "ExtractedCashflow", "ExtractedMutation", "ExtractionError"]:
        assert hasattr(gp, name), f"missing {name}"

    assert hasattr(gp, "GoalType")


def test_internal_types_not_exported():
    """RunContext, MortgageSchedule, etc. are engine-private."""
    import goal_planning as gp
    assert not hasattr(gp, "RunContext")
    assert not hasattr(gp, "MortgageSchedule")
    assert not hasattr(gp, "GoalInternal")
    assert not hasattr(gp, "FundingResult")
