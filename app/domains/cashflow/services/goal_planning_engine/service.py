"""Goal-planning service — runs the cashflow engine and builds a facts_pack.

Bridges the ``AI_Agents/src/cashflow_statement`` engine into chat. The output
``GoalPlanningServiceOutcome`` carries both the LLM-ready ``facts_pack`` dict
and a deterministic ``fallback_text`` for when the formatter LLM fails.

Deliberately NOT bridged: ``cashflow_statement.summarizer``. See the note in
``compute_goal_planning_snapshot``.
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
    overrides: dict[str, Any] | None = None,
) -> GoalPlanningServiceOutcome:
    """Run the cashflow engine for the given user and produce a facts_pack.

    ``overrides`` makes this a COUNTERFACTUAL run — "what if I retire at 50?".
    The engine is pure Python, so the honest way to answer is to rebuild the
    input with the field changed and run it again. Allowed keys are whitelisted
    in ``overrides.ALLOWED_OVERRIDE_KEYS``.

    A counterfactual run does NOT persist. Every base turn writes ~30 rows and
    the Goal Planning screen reads the latest one, so persisting a hypothetical
    would overwrite the customer's real plan.

    Raises ValueError("missing_date_of_birth") or
    ValueError("missing_required_inputs:<comma-separated keys>") when the user
    profile is incomplete — the engine never substitutes default/placeholder
    values for missing inputs. Raises ValueError on an unknown override key.
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
        from app.domains.portfolio.services.portfolio_service import (
            get_primary_portfolio,
        )

        portfolio = await get_primary_portfolio(db, user.id)
        if portfolio is not None and portfolio.total_value is not None:
            portfolio_value = float(portfolio.total_value)
    except Exception:
        logger.warning(
            "portfolio value lookup failed; using cash & assets only", exc_info=True
        )

    # Builder is sync and returns the fully-constructed GoalPlanningInput plus
    # a debug dict. Don't await it and don't try to re-construct from kwargs.
    # (The builder logs the resolved inputs; we log the trigger/output here.)
    try:
        gp_input, _debug = build_goal_planning_input_for_user(
            user, anchor_date, portfolio_value=portfolio_value
        )
    except ValueError as e:
        log_skipped(
            path="chat", user_id=user.id, error=str(e), session_id=chat_session_id
        )
        raise

    from app.domains.cashflow.services.goal_planning_engine.overrides import (
        apply_overrides,
    )

    # Same object back when there are no overrides — that identity IS the
    # base-vs-counterfactual test used to gate persistence below.
    gp_input = apply_overrides(gp_input, overrides)
    is_counterfactual = bool(overrides)

    from cashflow_statement.models import GoalPlanningOutput
    from cashflow_statement.engine import compute_full_projection

    output: GoalPlanningOutput = await asyncio.to_thread(
        compute_full_projection, gp_input
    )

    # No ``summarize_plan`` here on purpose. That second Haiku call narrated the
    # engine output into bullets, which then went into facts_pack["narrative"]
    # for the formatter to narrate again — and it ran question_aware=False, so its
    # prose pulled against the formatter's brief to answer what was asked.
    #
    # This was its last production caller. The REST path builds its snapshot from
    # ``compute_full_projection`` (cashflow_compute_service), which never populates
    # ``GoalPlanningOutput.summary``, so ``persist_plan_run``'s summary branch has
    # never fired — ``cashflow_plan_summary`` holds 0 rows against 321 plan runs.
    # ``summarize_plan`` now runs only in ``dev_run.py`` and the LangGraph agent,
    # neither of which is wired into ``app/``.

    # A hypothetical is never written down — see the docstring.
    plan_run_id = (
        None
        if is_counterfactual
        else await _persist_plan_run(db, user.id, chat_session_id, output)
    )

    log_output(
        path="chat",
        user_id=user.id,
        output=output,
        plan_run_id=plan_run_id,
        session_id=chat_session_id,
    )

    facts_pack = _build_facts_pack(
        output,
        user,
        retirement_age=gp_input.retirement.retirement_age,
        retirement_modelled=gp_input.model_retirement,
    )
    fallback_text = _build_fallback_text(output)

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
        db.add(
            CashflowHeadline(
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
            )
        )

        ffs = output.fund_flow_summary
        db.add(
            CashflowFundFlowSummary(
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
            )
        )

        for row in output.annual_cashflow:
            db.add(
                CashflowAnnualRow(
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
                )
            )

        await db.flush()
        return run.id

    except Exception:
        logger.exception("Failed to persist cashflow plan run for user_id=%s", user_id)
        return None


def _retirement_facts(
    retirement: Any,
    *,
    retirement_age: int | None,
    retirement_modelled: bool,
) -> dict[str, Any]:
    """The retirement block, scoped to what the plan actually uses.

    ``retirement_age`` is supplied rather than derived: the formatter is told to
    quote facts and never compute, and with no age in the pack it invented one
    ("retire at 52" for a customer retiring at 55).

    The corpus figures are included ONLY when the engine models retirement as a
    goal. On the product path it does not (``input_builder`` sets
    ``model_retirement=False``), so ``corpus_required_used`` never reaches the
    funding math — ``goals_table.py`` uses it only under ``include_retirement``.
    Shipping it anyway put a second, unrelated "retirement corpus" next to the
    customer's own Retirement goal, and the two differed threefold.
    """
    # Field names do the disambiguating. A plain ``retirement_date`` sitting next
    # to a customer goal named "Retirement" got blended: for a user retiring at 55
    # in 2047 whose own goal is dated 2052, the reply read "you retire on 8 July
    # 2052 (age 52)" — day and month from here, year from the goal, and the year
    # misread as an age. Self-describing keys leave nothing to blend.
    facts: dict[str, Any] = {
        "planned_retirement_date_from_profile": str(retirement.retirement_date),
        "planned_retirement_age_from_profile": retirement_age,
        "years_to_planned_retirement": retirement.years_to_retirement,
        "annual_household_expense_today": retirement.annual_household_expense_today,
        "annual_household_expense_today_indian": format_inr_indian(
            retirement.annual_household_expense_today
        ),
        "post_retirement_years": retirement.post_retirement_years,
        "is_funded_as_a_goal": retirement_modelled,
    }
    if retirement_modelled:
        facts["corpus_required_used"] = retirement.corpus_required_used
        facts["corpus_required_used_indian"] = format_inr_indian(
            retirement.corpus_required_used
        )
        facts["corpus_required_pv_today"] = retirement.corpus_required_pv_today
        facts["corpus_required_pv_today_indian"] = format_inr_indian(
            retirement.corpus_required_pv_today
        )
    return facts


def _build_facts_pack(
    output: Any,
    user: User,
    *,
    retirement_age: int | None = None,
    retirement_modelled: bool = False,
) -> dict[str, Any]:
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
            "total_corpus_required_today_indian": format_inr_indian(
                headline.total_corpus_required_today
            ),
            "surplus_or_shortfall_today": headline.surplus_or_shortfall_today,
            "surplus_or_shortfall_today_indian": format_inr_indian(
                headline.surplus_or_shortfall_today
            ),
            "corpus_closing": headline.corpus_closing,
            "corpus_closing_indian": format_inr_indian(headline.corpus_closing),
            "is_feasible": headline.is_feasible,
            "total_shortfall_fv": headline.total_shortfall_fv,
            "total_shortfall_fv_indian": format_inr_indian(headline.total_shortfall_fv),
            "total_funded_amount": headline.total_funded_amount,
            "total_funded_amount_indian": format_inr_indian(
                headline.total_funded_amount
            ),
        },
        "retirement": _retirement_facts(
            retirement,
            retirement_age=retirement_age,
            retirement_modelled=retirement_modelled,
        ),
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
        facts["goals"].append(
            {
                "name": g.name,
                "goal_type": g.goal_type.value
                if hasattr(g.goal_type, "value")
                else str(g.goal_type),
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
                    "funded"
                    if g.is_funded
                    else ("unfunded" if g.funded_amount == 0 else "partially_funded")
                ),
            }
        )

    # Annual cashflow rows for the formatter LLM — reference material for
    # year-specific questions, NOT a table to render (the reply never reproduces
    # the year-by-year statement; the frontend charts it). The EMI splits and
    # corpus_opening are persisted in CashflowAnnualRow but aren't useful to the
    # formatter, so they're omitted here to keep the prompt small.
    facts["annual_cashflow"] = []
    for row in output.annual_cashflow:
        facts["annual_cashflow"].append(
            {
                "fy_label": row.fy_label,
                "income": format_inr_indian(row.income),
                "income_tax": format_inr_indian(row.income_tax),
                "household_expense": format_inr_indian(row.household_expense),
                "savings_pre_emi": format_inr_indian(row.savings_pre_emi),
                "savings_post_emi": format_inr_indian(row.savings_post_emi),
                "monthly_investment": format_inr_indian(row.monthly_investment),
                "investment_returns": format_inr_indian(row.investment_returns),
                "goal_payout": format_inr_indian(row.goal_payout),
                "corpus_closing": format_inr_indian(row.corpus_closing),
                "is_funded": row.is_funded,
            }
        )


    return facts


def _build_fallback_text(output: Any) -> str:
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

    return "\n".join(lines)
