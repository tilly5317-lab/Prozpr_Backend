"""Goal-planning service — runs the cashflow engine and builds a facts_pack.

Bridges ``AI_Agents/src/cashflow_statement`` engine + summarizer into chat.
The output ``GoalPlanningServiceOutcome`` carries both the LLM-ready
``facts_pack`` dict and a deterministic ``fallback_text`` for when the
formatter LLM fails.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity.models.user import User
from app.domains.ai_engine.common import ensure_ai_agents_path, format_inr_indian
from app.domains.cashflow.services.goal_planning_engine.cashflow_trace import (
    log_output,
    log_skipped,
    log_trigger,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GoalPlanningServiceOutcome:
    """Result of a goal-planning engine run, ready for the answer-formatter."""
    facts_pack: dict[str, Any]
    fallback_text: str
    # Raw engine output (`cashflow_statement.GoalPlanningOutput`). The chat
    # handler reads `snapshot.annual_cashflow` / `snapshot.monthly_cashflow`
    # to build chart payloads — keep this even though the facts_pack already
    # mirrors most of it as Indian-notation strings.
    snapshot: Any = None
    plan_run_id: uuid.UUID | None = None


async def compute_goal_planning_snapshot(
    *,
    user: User,
    user_question: str,
    chat_session_id: str,
    anchor_date: date,
    db: AsyncSession | None = None,
) -> GoalPlanningServiceOutcome:
    """Run the cashflow engine for the given user and produce a facts_pack.

    Raises ValueError("missing_date_of_birth") or
    ValueError("missing_required_inputs:<comma-separated keys>") when the user
    profile is incomplete — the engine never substitutes default/placeholder
    values for missing inputs.
    """
    ensure_ai_agents_path()

    from app.domains.cashflow.services.goal_planning_engine.input_builder import (
        build_goal_planning_input_for_user,
    )

    log_trigger(path="chat", user_id=user.id, session_id=chat_session_id)

    if db is None:
        raise ValueError("Database session required for goal planning")

    # Current portfolio value feeds the engine's starting corpus (single source of
    # truth — the portfolio/CAMS data), summed with cash & assets.
    portfolio_value = None
    try:
        from app.domains.portfolio.services.portfolio_service import get_primary_portfolio

        portfolio = await get_primary_portfolio(db, user.id)
        if portfolio is not None and portfolio.total_value is not None:
            portfolio_value = float(portfolio.total_value)
    except Exception:
        logger.warning("portfolio value lookup failed; using cash & assets only", exc_info=True)

    # Builder is sync and returns the fully-constructed GoalPlanningInput plus
    # a debug dict. Don't await it and don't try to re-construct from kwargs.
    # (The builder logs the resolved inputs; we log the trigger/output here.)
    try:
        gp_input, _debug = build_goal_planning_input_for_user(
            user, anchor_date, portfolio_value=portfolio_value
        )
    except ValueError as e:
        log_skipped(path="chat", user_id=user.id, error=str(e), session_id=chat_session_id)
        raise

    from cashflow_statement.models import GoalPlanningOutput
    from cashflow_statement.engine import compute_full_projection
    from cashflow_statement.summarizer import summarize_plan

    output: GoalPlanningOutput = await asyncio.to_thread(compute_full_projection, gp_input)

    summary = None
    try:
        summary = await asyncio.to_thread(summarize_plan, output)
    except Exception:
        logger.warning("summarize_plan failed; proceeding without narrative", exc_info=True)

    plan_run_id = await _persist_plan_run(db, user.id, chat_session_id, output)

    log_output(
        path="chat",
        user_id=user.id,
        output=output,
        plan_run_id=plan_run_id,
        session_id=chat_session_id,
    )

    facts_pack = _build_facts_pack(output, summary, user)
    fallback_text = _build_fallback_text(output, summary)

    return GoalPlanningServiceOutcome(
        facts_pack=facts_pack,
        fallback_text=fallback_text,
        snapshot=output,
        plan_run_id=plan_run_id,
    )


async def _persist_plan_run(
    db: AsyncSession,
    user_id: uuid.UUID,
    chat_session_id: str,
    output: Any,
) -> uuid.UUID | None:
    """Persist the engine run to cashflow_plan_runs and child tables."""
    try:
        from app.domains.cashflow.models import (
            CashflowAnnualRow,
            CashflowFundFlowSummary,
            CashflowHeadline,
            CashflowPlanRun,
        )

        run = CashflowPlanRun(
            user_id=user_id,
            chat_session_id=uuid.UUID(chat_session_id) if chat_session_id else None,
            engine_version=output.engine_version,
            assumption_id=uuid.uuid4(),
            warnings=output.warnings or [],
            computed_at=output.computed_at,
        )
        db.add(run)
        await db.flush()

        headline = output.headline
        db.add(CashflowHeadline(
            plan_run_id=run.id,
            user_id=user_id,
            years_to_last_goal=headline.years_to_last_goal,
            last_goal_date=headline.last_goal_date,
            last_fy_end_date=headline.last_fy_end_date,
            number_of_goals=headline.number_of_goals,
            corpus_today=headline.corpus_today,
            total_corpus_required_today=headline.total_corpus_required_today,
            surplus_or_shortfall_today=headline.surplus_or_shortfall_today,
            corpus_closing=headline.corpus_closing,
            total_shortfall_fv=headline.total_shortfall_fv,
            total_funded_amount=headline.total_funded_amount,
        ))

        ffs = output.fund_flow_summary
        db.add(CashflowFundFlowSummary(
            plan_run_id=run.id,
            user_id=user_id,
            corpus_opening=ffs.corpus_opening,
            total_investments=ffs.total_investments,
            total_roi=ffs.total_roi,
            total_one_off_in=ffs.total_one_off_in,
            total_one_off_out=ffs.total_one_off_out,
            total_goals_paid=ffs.total_goals_paid,
            corpus_closing=ffs.corpus_closing,
            corpus_today=ffs.corpus_today,
            total_corpus_required_today=ffs.total_corpus_required_today,
            surplus_or_shortfall_today=ffs.surplus_or_shortfall_today,
        ))

        for row in output.annual_cashflow:
            db.add(CashflowAnnualRow(
                plan_run_id=run.id,
                user_id=user_id,
                fy_end_date=row.fy_end_date,
                fy_label=row.fy_label,
                income=row.income,
                income_tax=row.income_tax,
                household_expense=row.household_expense,
                savings_pre_emi=row.savings_pre_emi,
                existing_mortgage_emi=row.existing_mortgage_emi,
                goal_mortgage_emi=row.goal_mortgage_emi,
                savings_post_emi=row.savings_post_emi,
                one_off_inflow=row.one_off_inflow,
                one_off_outflow=row.one_off_outflow,
                corpus_opening=row.corpus_opening,
                monthly_investment=row.monthly_investment,
                investment_returns=row.investment_returns,
                goal_payout=row.goal_payout,
                corpus_closing=row.corpus_closing,
                is_funded=row.is_funded,
            ))

        await db.flush()
        return run.id

    except Exception:
        logger.exception("Failed to persist cashflow plan run for user_id=%s", user_id)
        return None


def _build_facts_pack(output: Any, summary: Any, user: User) -> dict[str, Any]:
    """Build the facts_pack dict consumed by the answer-formatter LLM."""

    headline = output.headline
    retirement = output.retirement
    fund_flow = output.fund_flow_summary

    facts: dict[str, Any] = {
        "headline": {
            "years_to_last_goal": headline.years_to_last_goal,
            "last_goal_date": str(headline.last_goal_date),
            "number_of_goals": headline.number_of_goals,
            "corpus_today": headline.corpus_today,
            "corpus_today_indian": format_inr_indian(headline.corpus_today),
            "total_corpus_required_today": headline.total_corpus_required_today,
            "total_corpus_required_today_indian": format_inr_indian(headline.total_corpus_required_today),
            "surplus_or_shortfall_today": headline.surplus_or_shortfall_today,
            "surplus_or_shortfall_today_indian": format_inr_indian(headline.surplus_or_shortfall_today),
            "corpus_closing": headline.corpus_closing,
            "corpus_closing_indian": format_inr_indian(headline.corpus_closing),
            "is_feasible": headline.is_feasible,
            "total_shortfall_fv": headline.total_shortfall_fv,
            "total_shortfall_fv_indian": format_inr_indian(headline.total_shortfall_fv),
            "total_funded_amount": headline.total_funded_amount,
            "total_funded_amount_indian": format_inr_indian(headline.total_funded_amount),
        },
        "retirement": {
            "retirement_date": str(retirement.retirement_date),
            "years_to_retirement": retirement.years_to_retirement,
            "corpus_required_used": retirement.corpus_required_used,
            "corpus_required_used_indian": format_inr_indian(retirement.corpus_required_used),
            "corpus_required_pv_today": retirement.corpus_required_pv_today,
            "corpus_required_pv_today_indian": format_inr_indian(retirement.corpus_required_pv_today),
            "annual_household_expense_today": retirement.annual_household_expense_today,
            "annual_household_expense_today_indian": format_inr_indian(retirement.annual_household_expense_today),
            "post_retirement_years": retirement.post_retirement_years,
        },
        "cashflow_horizon": {
            "corpus_opening": fund_flow.corpus_opening,
            "corpus_opening_indian": format_inr_indian(fund_flow.corpus_opening),
            "total_investments": fund_flow.total_investments,
            "total_investments_indian": format_inr_indian(fund_flow.total_investments),
            "total_roi": fund_flow.total_roi,
            "total_roi_indian": format_inr_indian(fund_flow.total_roi),
            "total_one_off_in": fund_flow.total_one_off_in,
            "total_one_off_in_indian": format_inr_indian(fund_flow.total_one_off_in),
            "total_one_off_out": fund_flow.total_one_off_out,
            "total_one_off_out_indian": format_inr_indian(fund_flow.total_one_off_out),
            "total_goals_paid": fund_flow.total_goals_paid,
            "total_goals_paid_indian": format_inr_indian(fund_flow.total_goals_paid),
            "corpus_closing": fund_flow.corpus_closing,
            "corpus_closing_indian": format_inr_indian(fund_flow.corpus_closing),
        },
        "goals": [],
        "validation_issues": [],
    }

    for g in output.goals:
        facts["goals"].append({
            "name": g.name,
            "goal_type": g.goal_type.value if hasattr(g.goal_type, "value") else str(g.goal_type),
            "goal_date": str(g.goal_date),
            "goal_value_fv": g.goal_value_fv,
            "goal_value_fv_indian": format_inr_indian(g.goal_value_fv),
            "corpus_required_fv": g.corpus_required_fv,
            "corpus_required_fv_indian": format_inr_indian(g.corpus_required_fv),
            "funded_amount": g.funded_amount,
            "funded_amount_indian": format_inr_indian(g.funded_amount),
            "is_funded": g.is_funded,
            "shortfall_fv": g.shortfall_fv,
            "shortfall_fv_indian": format_inr_indian(g.shortfall_fv),
            "verdict": (
                "funded" if g.is_funded
                else ("unfunded" if g.funded_amount == 0 else "partially_funded")
            ),
        })

    # Annual cashflow table
    facts["annual_cashflow"] = []
    for row in output.annual_cashflow:
        facts["annual_cashflow"].append({
            "fy_label": row.fy_label,
            "income": format_inr_indian(row.income),
            "income_tax": format_inr_indian(row.income_tax),
            "household_expense": format_inr_indian(row.household_expense),
            "savings_pre_emi": format_inr_indian(row.savings_pre_emi),
            "existing_mortgage_emi": format_inr_indian(row.existing_mortgage_emi),
            "goal_mortgage_emi": format_inr_indian(row.goal_mortgage_emi),
            "savings_post_emi": format_inr_indian(row.savings_post_emi),
            "corpus_opening": format_inr_indian(row.corpus_opening),
            "monthly_investment": format_inr_indian(row.monthly_investment),
            "investment_returns": format_inr_indian(row.investment_returns),
            "goal_payout": format_inr_indian(row.goal_payout),
            "corpus_closing": format_inr_indian(row.corpus_closing),
            "is_funded": row.is_funded,
        })

    if summary:
        facts["narrative"] = {
            "top_line": summary.top_line,
            "retirement_note": summary.retirement_note,
            "cashflow_note": summary.cashflow_note,
            "risks": summary.risks,
            "next_steps": summary.next_steps,
            "goals": [
                {
                    "name": gb.name,
                    "verdict": gb.verdict,
                    "headline_amount": gb.headline_amount,
                    "note": gb.note,
                }
                for gb in summary.goals
            ],
        }
        facts["next_steps"] = summary.next_steps
    else:
        facts["narrative"] = None
        facts["next_steps"] = []

    return facts


def _build_fallback_text(output: Any, summary: Any) -> str:
    """Deterministic fallback text when the formatter LLM fails."""

    headline = output.headline
    lines = []

    if headline.is_feasible:
        lines.append("Your financial plan looks on track.")
    else:
        shortfall = format_inr_indian(headline.total_shortfall_fv) or "some amount"
        lines.append(f"Your plan has a shortfall of {shortfall} across your goals.")

    lines.append(
        f"Corpus today: {format_inr_indian(headline.corpus_today) or '₹0'} | "
        f"Required: {format_inr_indian(headline.total_corpus_required_today) or '₹0'}"
    )

    funded_count = sum(1 for g in output.goals if g.is_funded)
    total_count = len(output.goals)
    lines.append(f"{funded_count}/{total_count} goals are fully funded.")

    if summary and summary.top_line:
        lines.append("")
        lines.append(summary.top_line)

    if summary and summary.next_steps:
        lines.append("")
        lines.append("**Next steps:**")
        for step in summary.next_steps[:3]:
            lines.append(f"- {step}")

    return "\n".join(lines)
