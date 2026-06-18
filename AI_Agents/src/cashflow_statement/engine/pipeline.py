"""Public engine entry: 8-stage orchestrator. Imports nothing LLM-related."""

from __future__ import annotations
from datetime import date, datetime, timezone

from cashflow_statement.engine import profile as profile_module

from cashflow_statement.models import (
    GoalPlanningInput,
    GoalPlanningOutput,
    ValidationIssue,
)
from cashflow_statement.engine.profile import build_initial_context
from cashflow_statement.engine.retirement import compute_retirement_snapshot
from cashflow_statement.engine.mortgages import build_existing_mortgages
from cashflow_statement.engine.properties import (
    build_goal_properties,
    build_goal_property_details,
)
from cashflow_statement.engine.goals_table import build_goals_table
from cashflow_statement.engine.cashflow import (
    project_cashflow,
    derive_annual_cashflow,
    compute_horizon_years,
)
from cashflow_statement.engine.dates import eomonth
from cashflow_statement.engine.funding import compute_funding
from cashflow_statement.engine.summary import (
    build_headline_status,
    build_fund_flow_summary,
)

# Bump on any change to the projection math/semantics so cached plan runs
# auto-recompute (the cashflow router recomputes when a stored run's
# engine_version != this). 0.2.0: retirement is no longer auto-injected as a
# goal (model_retirement) — see build_goals_table / input_builder. 0.3.0: the
# projection horizon now always runs to max(last_goal, retirement_date) even when
# retirement is not modelled, so the cashflow always reaches the planned
# retirement age (see horizon block below).
ENGINE_VERSION = "0.3.0"


def compute_full_projection(input: GoalPlanningInput) -> GoalPlanningOutput:
    # Fail fast on input errors before any computation.
    issues = validate_input_only(input)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        msg = "; ".join(f"{i.field}: {i.message}" for i in errors)
        raise ValueError(f"GoalPlanningInput validation failed: {msg}")

    warnings: list[str] = []

    ctx = build_initial_context(input.profile, input.assumptions)  # 1
    retirement = compute_retirement_snapshot(input.retirement, ctx, warnings)  # 2a
    # `with_retirement` sets retirement_date_considered, which stops salary income
    # at retirement (cashflow.py / funding.py). When retirement is NOT modelled,
    # leave it None so income continues for the whole projection.
    if input.model_retirement:
        ctx = ctx.with_retirement(retirement)  # 2b

    # Horizon. The projection ALWAYS runs until the later of the retirement date
    # and the last goal — i.e. max(last_goal_year, retirement_age). Retirement age
    # comes from the goal-planning inputs (RetirementInput, default 60); the last
    # goal contributes nothing when the user has no goals (last_goal_date falls
    # back to retire_date). So:
    #   - no goals / goals before retirement  -> horizon = retirement date
    #   - a goal after retirement             -> horizon = that goal's date
    # This holds whether or not retirement is *modelled* as a goal: model_retirement
    # only governs stopping salary income + injecting the retirement corpus payout
    # (gated above / in build_goals_table), NOT the horizon length. Only custom
    # goals and goal properties can push the horizon past retirement; one-off
    # outflows do NOT — a one-off scheduled beyond the projection end is dropped,
    # so warn the caller.
    retire_date = retirement.retirement_date
    goal_dates = [gp.goal_date for gp in input.goal_properties] + [
        cg.goal_date for cg in input.custom_goals
    ]
    last_goal_date = max(goal_dates, default=retire_date)
    projection_end = max(retire_date, last_goal_date)
    projection_end_month_end = eomonth(
        date(projection_end.year, projection_end.month, 1), 0
    )

    for e in input.one_off_outflows:
        if e.date > projection_end_month_end:
            warnings.append(
                f"one_off_outflow '{e.description}' date={e.date} is after the "
                f"projection end {projection_end_month_end}; dropped from projection."
            )

    horizon = compute_horizon_years(
        retirement_date=retire_date,
        latest_update_date=ctx.latest_update_date,
        last_goal_date=last_goal_date,
        cap=ctx.horizon_cap_years,
    )
    horizon_end = date(ctx.current_fy_year + horizon, 3, 31)

    existing_mortgages = build_existing_mortgages(
        input.current_properties, ctx, warnings
    )
    goal_property_outcomes = build_goal_properties(
        input.goal_properties, ctx, horizon_end, warnings
    )
    goal_property_details = build_goal_property_details(
        goal_property_outcomes,
        input.goal_properties,
        ctx,
    )

    goals_internal = build_goals_table(
        retirement,
        goal_property_outcomes,
        input.custom_goals,
        ctx,
        input.assumptions,
        warnings,
        include_retirement=input.model_retirement,
    )  # 5

    monthly_pre_funding = project_cashflow(
        ctx,
        existing_mortgages,
        [g.amortization for g in goal_property_outcomes if g.amortization],
        input.one_off_inflows,
        input.one_off_outflows,
        years_to_last_goal=horizon,
    )  # 6a

    # Truncate at the end of the projection month (later of retirement and the
    # last goal). With no post-retirement goals this is the retirement month —
    # identical to the prior behaviour. With a later goal, the extra months let
    # the funding stage pay it out of the corpus remaining after the retirement
    # payout; months beyond the last goal would only show stuck-corpus noise.
    monthly_pre_funding = [
        r for r in monthly_pre_funding if r.month_end_date <= projection_end_month_end
    ]  # 6b

    funding = compute_funding(
        goals_internal,
        ctx,
        monthly_pre_funding,
        input.one_off_inflows,
        input.one_off_outflows,
        warnings,
    )  # 7

    # Annual is derived from the funding-enriched monthly so it carries both
    # P&L sums and corpus evolution aggregates.
    monthly_cashflow = funding.monthly_enriched
    annual_cashflow = derive_annual_cashflow(monthly_cashflow)  # 8a

    headline = build_headline_status(
        ctx, goals_internal, funding, input.one_off_outflows
    )  # 8b
    fund_flow = build_fund_flow_summary(ctx, goals_internal, funding)

    full = input.detail_level == "full"

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
        # The enriched rows are the canonical monthly view (cashflow + corpus combined).
        monthly_cashflow=funding.monthly_enriched if full else None,
        warnings=warnings,
        computed_at=datetime.now(timezone.utc),
    )


def validate_input_only(input: GoalPlanningInput) -> list[ValidationIssue]:
    """Pre-flight check: cheap validation; no projection run.

    Past-date checks use the engine's notion of "today" via `profile._current_date()`,
    so tests can mock it consistently with `build_initial_context`.
    """
    issues: list[ValidationIssue] = []
    today = profile_module._current_date()

    for g in input.custom_goals:
        if g.goal_date <= today:
            issues.append(
                ValidationIssue(
                    field=f"custom_goals[{g.name}].goal_date",
                    message=f"goal_date {g.goal_date} is in the past (today={today})",
                    severity="error",
                )
            )
    for g in input.goal_properties:
        if g.goal_date <= today:
            issues.append(
                ValidationIssue(
                    field=f"goal_properties[{g.name}].goal_date",
                    message=f"goal_date {g.goal_date} is in the past",
                    severity="error",
                )
            )

    return issues
