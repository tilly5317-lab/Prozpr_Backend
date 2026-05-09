"""Public engine entry: 8-stage orchestrator. Imports nothing LLM-related."""
from __future__ import annotations
from datetime import datetime

from goal_planning.models import (
    GoalPlanningInput, GoalPlanningOutput, GoalPropertyDetail,
    MortgageAmortization, ValidationIssue,
)
from goal_planning.engine.profile import build_initial_context
from goal_planning.engine.retirement import compute_retirement_snapshot
from goal_planning.engine.mortgages import build_existing_mortgages
from goal_planning.engine.properties import build_goal_properties
from goal_planning.engine.goals_table import build_goals_table
from goal_planning.engine.cashflow import project_cashflow, compute_horizon_years
from goal_planning.engine.funding import compute_funding
from goal_planning.engine.summary import build_headline_status, build_fund_flow_summary

ENGINE_VERSION = "0.1.0"


def compute_full_projection(input: GoalPlanningInput) -> GoalPlanningOutput:
    warnings: list[str] = []

    ctx = build_initial_context(input.profile, input.assumptions)                              # 1
    retirement = compute_retirement_snapshot(input.retirement, ctx, warnings)                  # 2a
    ctx = ctx.with_retirement(retirement)                                                      # 2b

    existing_mortgages = build_existing_mortgages(input.current_properties, ctx, warnings)     # 3
    goal_property_outcomes = build_goal_properties(input.goal_properties, ctx, warnings)       # 4

    # Build public-facing GoalPropertyDetail list (lifts internal outcome + source spec).
    goal_property_details: list[GoalPropertyDetail] = []
    name_to_goalprop = {gp.name: gp for gp in input.goal_properties}
    for outcome in goal_property_outcomes:
        src = name_to_goalprop[outcome.name]
        if outcome.amortization is not None:
            emi = outcome.amortization.monthly_rows[0].emi
            total_interest = sum(r.interest_portion for r in outcome.amortization.monthly_rows)
            payoff_date = outcome.amortization.monthly_rows[-1].month_end
        else:
            emi = None
            total_interest = None
            payoff_date = None
        goal_property_details.append(GoalPropertyDetail(
            name=outcome.name,
            target_pv=src.target_pv if src.target_pv is not None else outcome.target_fv,
            target_fv=outcome.target_fv,
            payout_amount_fv=outcome.payout_amount_fv,
            is_downpayment_only=src.is_downpayment_only,
            upfront_amount=src.upfront_amount,
            mortgage_amount=outcome.mortgage_amount,
            mortgage_tenure_years=src.mortgage_tenure_years,
            mortgage_interest_annual=src.mortgage_interest_annual,
            mortgage_emi_monthly=emi,
            mortgage_total_interest=total_interest,
            mortgage_payoff_date=payoff_date,
            goal_date=src.goal_date,
        ))

    goals_internal = build_goals_table(
        retirement, goal_property_outcomes, input.custom_goals, ctx, input.assumptions, warnings
    )                                                                                          # 5

    last_goal_fy = max(
        (g.goal_date_fy for g in goals_internal),
        default=ctx.current_fy_end,
    )
    horizon = compute_horizon_years(
        retirement_date=retirement.retirement_date,
        last_goal_fy=last_goal_fy,
        one_off_outflows=input.one_off_outflows,
        latest_update_date=ctx.latest_update_date,
        cap=ctx.horizon_cap_years,
    )
    monthly_cashflow, annual_cashflow = project_cashflow(
        ctx, existing_mortgages,
        [g.amortization for g in goal_property_outcomes if g.amortization],
        input.one_off_inflows, input.one_off_outflows,
        horizon_years=horizon, warnings=warnings,
    )                                                                                          # 6

    funding = compute_funding(
        goals_internal, ctx, monthly_cashflow, input.one_off_inflows, input.one_off_outflows,
        warnings,
    )                                                                                          # 7

    headline = build_headline_status(ctx, goals_internal, funding, retirement, annual_cashflow, warnings)  # 8
    fund_flow = build_fund_flow_summary(
        ctx, annual_cashflow, funding, input.one_off_inflows, input.one_off_outflows,
    )

    full = (input.detail_level == "full")
    if full:
        internal_schedules = (
            existing_mortgages + [g.amortization for g in goal_property_outcomes if g.amortization]
        )
        mortgage_schedules: list[MortgageAmortization] | None = [
            MortgageAmortization(
                property_ref=s.property_ref,
                start_date=s.start_date,
                monthly_schedule=s.monthly_rows,
            )
            for s in internal_schedules
        ]
    else:
        mortgage_schedules = None

    return GoalPlanningOutput(
        engine_version=ENGINE_VERSION,
        input_echo=input,
        headline=headline,
        retirement=retirement,
        goals=funding.per_goal_status,
        one_off_outflow_status=funding.per_one_off_outflow_status,
        annual_cashflow=annual_cashflow,
        fund_flow_summary=fund_flow,
        goal_property_details=goal_property_details,
        monthly_cashflow=monthly_cashflow if full else None,
        nfa_monthly_series=funding.nfa_monthly if full else None,
        mortgage_amortizations=mortgage_schedules,
        warnings=warnings,
        computed_at=datetime.utcnow(),
    )


def validate_input_only(input: GoalPlanningInput) -> list[ValidationIssue]:
    """Pre-flight check: cheap validation; no projection run."""
    issues: list[ValidationIssue] = []

    if input.retirement.date_of_birth is None:
        issues.append(ValidationIssue(
            field="retirement.date_of_birth",
            message="DOB required for retirement calc",
            severity="error",
        ))

    update_date = input.profile.latest_update_date
    for g in input.custom_goals:
        if g.goal_date <= update_date:
            issues.append(ValidationIssue(
                field=f"custom_goals[{g.name}].goal_date",
                message=f"goal_date {g.goal_date} is in the past (latest_update_date={update_date})",
                severity="error",
            ))
    for g in input.goal_properties:
        if g.goal_date <= update_date:
            issues.append(ValidationIssue(
                field=f"goal_properties[{g.name}].goal_date",
                message=f"goal_date {g.goal_date} is in the past",
                severity="error",
            ))

    return issues
