"""Chat handler for the GOAL_PLANNING intent.

Runs the input builder + cashflow engine for the turn's User, then hands the
resulting ``facts_pack`` to the shared answer-formatter. The formatter LLM
is the customer-facing voice — this module never templates user-visible
prose itself; it produces facts and lets the formatter speak.
"""

from __future__ import annotations

import logging
from datetime import date

from app.domains.ai_engine.answer_formatter import (
    format_relay_or_canned,
    format_with_telemetry,
)
from app.domains.ai_engine.chat_dispatcher import ChatHandlerResult, register
from app.domains.cashflow.services.goal_planning_engine.service import (
    compute_goal_planning_snapshot,
)
from app.domains.ai_engine.turn_context import TurnContext

logger = logging.getLogger(__name__)


_BODY_PROMPT = """You are answering a customer's question about their goal plan —
whether their goals are on track, and the cashflow projection behind that. The
shared house-style rules above apply.

Answer the question that was asked, at the depth it was asked, and then stop.

"Will I make my goals?" is a yes/no question. Answer it in a few sentences: the
verdict, one short line per goal saying where it stands, and any real caution.
Nothing else. That is a complete answer — resist adding the summary the customer
did not ask for. A narrow question ("does a ₹10 lakh trip next year fit?") gets a
correspondingly narrow answer.

Prose by default. Use a table ONLY when the customer explicitly asked to see
several goals or years side by side; two or three of them read better as
sentences. Never stack multiple summary tables in one reply — a headline table
plus a goals table plus a fund-flow table is the plan document in disguise.

The full year-by-year cashflow statement never belongs in chat, even when asked
for directly. It lives in the Goal Planning screen, and the app renders its chart
beside your reply. If the customer asks to see it, say where it lives, and answer
what they were actually after in a sentence or two — the trajectory, a turning
point, a specific year from `annual_cashflow`. Never render the table.

The FACTS_PACK has this shape (treat fields not present as unknown):

  headline — the plan-level verdict.
    is_feasible: bool — do all goals get funded on the current trajectory
    corpus_today / corpus_closing — corpus now, and at the end of the horizon
    total_corpus_required_today — what all goals need, in today's rupees
    surplus_or_shortfall_today — negative means a present-value gap, which is
      NORMAL and not a failure when is_feasible is true: future contributions and
      returns close it. Never report it as "you are short" on a feasible plan.
    total_shortfall_fv / total_funded_amount, number_of_goals,
    years_to_last_goal, last_goal_date

  goals[] — one entry per goal, the usual subject of a goal question.
    name, goal_type, goal_date
    goal_value_fv — cost at the goal date; corpus_required_fv — what must be saved
    funded_amount, shortfall_fv
    is_funded: bool; verdict: "funded" | "partially_funded" | "unfunded"

  retirement — retirement_date, years_to_retirement, corpus_required_used,
    corpus_required_pv_today, annual_household_expense_today,
    post_retirement_years

  cashflow_horizon — totals across the whole projection: corpus_opening,
    total_investments, total_roi, total_one_off_in, total_one_off_out,
    total_goals_paid, corpus_closing

  annual_cashflow[] — one row per financial year: fy_label, income, income_tax,
    household_expense, savings_pre_emi, savings_post_emi, monthly_investment,
    investment_returns, goal_payout, corpus_closing, is_funded. Reference
    material for year-specific questions; not something to render wholesale.

  narrative — may be null. top_line, retirement_note, cashflow_note, risks[],
    next_steps[], goals[] (name, verdict, headline_amount, note)

  next_steps — suggested actions; surface one only if it genuinely follows.

  validation_issues — engine warnings worth raising if they bear on the question.

Every rupee amount has a pre-formatted `_indian` sibling — use it verbatim; never
re-derive or convert lakh/crore yourself. Quote only what is in the FACTS_PACK.
"""


def _build_cashflow_chart_payloads(snapshot) -> list[dict]:
    """Build chart_payloads for the frontend from the cashflow snapshot."""
    annual = snapshot.annual_cashflow
    if not annual:
        return []

    return [
        {
            "type": "cashflow_annual_bar",
            "title": "Annual Cashflow Projection",
            "data": [
                {
                    "fy_label": row.fy_label,
                    "income": float(row.income),
                    "household_expense": float(row.household_expense),
                    "savings_post_emi": float(row.savings_post_emi),
                    "corpus_closing": float(row.corpus_closing),
                    "monthly_investment": float(row.monthly_investment),
                    "goal_payout": float(row.goal_payout),
                }
                for row in annual
            ],
            "annual_cashflow": [
                {
                    "fy_end_date": str(row.fy_end_date),
                    "fy_label": row.fy_label,
                    "income": float(row.income),
                    "income_tax": float(row.income_tax),
                    "household_expense": float(row.household_expense),
                    "savings_pre_emi": float(row.savings_pre_emi),
                    "existing_mortgage_emi": float(row.existing_mortgage_emi),
                    "goal_mortgage_emi": float(row.goal_mortgage_emi),
                    "savings_post_emi": float(row.savings_post_emi),
                    "one_off_inflow": float(row.one_off_inflow),
                    "one_off_outflow": float(row.one_off_outflow),
                    "corpus_opening": float(row.corpus_opening),
                    "monthly_investment": float(row.monthly_investment),
                    "investment_returns": float(row.investment_returns),
                    "goal_payout": float(row.goal_payout),
                    "corpus_closing": float(row.corpus_closing),
                    "is_funded": row.is_funded,
                }
                for row in annual
            ],
            "monthly_cashflow": [
                {
                    "month_end_date": str(row.month_end_date),
                    "fy_label": row.fy_label,
                    "income": float(row.income),
                    "income_tax": float(row.income_tax),
                    "household_expense": float(row.household_expense),
                    "savings_pre_emi": float(row.savings_pre_emi),
                    "existing_mortgage_emi": float(row.existing_mortgage_emi),
                    "goal_mortgage_emi": float(row.goal_mortgage_emi),
                    "savings_post_emi": float(row.savings_post_emi),
                    "one_off_inflow": float(row.one_off_inflow),
                    "one_off_outflow": float(row.one_off_outflow),
                    "corpus_opening": float(row.corpus_opening),
                    "monthly_investment": float(row.monthly_investment),
                    "investment_source": row.investment_source,
                    "investment_returns": float(row.investment_returns),
                    "goal_payout": float(row.goal_payout),
                    "corpus_closing": float(row.corpus_closing),
                    "is_funded": row.is_funded,
                }
                for row in (snapshot.monthly_cashflow or [])
            ],
        }
    ]


@register("goal_planning")
async def goal_planning_chat(ctx: TurnContext) -> ChatHandlerResult:
    """Single chat handler — runs the cashflow engine, formats the reply."""
    try:
        outcome = await compute_goal_planning_snapshot(
            user=ctx.user_ctx,
            user_question=ctx.user_question,
            chat_session_id=str(ctx.session_id),
            anchor_date=date.today(),
            db=ctx.db,
        )
    except ValueError as e:
        if str(e) == "missing_date_of_birth":
            text = await format_relay_or_canned(
                ctx=ctx,
                module_name="goal_planning",
                message=(
                    "To run a goal projection for you, I'll need your date of "
                    "birth — it anchors the math. Add it in settings, and "
                    "we'll pick this up right away."
                ),
            )
            return ChatHandlerResult(text=text)
        if str(e) == "missing_financial_profile":
            text = await format_relay_or_canned(
                ctx=ctx,
                module_name="goal_planning",
                message=(
                    "I need your financial profile to run a cashflow projection — "
                    "things like annual income, expenses, and current assets. "
                    "Please update your profile and we'll get this done."
                ),
            )
            return ChatHandlerResult(text=text)
        if str(e).startswith("missing_required_inputs:"):
            from app.domains.cashflow.services.goal_planning_engine.readiness import (
                REQUIRED_CASHFLOW_FIELDS,
            )

            labels = {f.key: f.label for f in REQUIRED_CASHFLOW_FIELDS}
            keys = [k for k in str(e).split(":", 1)[1].split(",") if k]
            needed = ", ".join(labels.get(k, k) for k in keys)
            text = await format_relay_or_canned(
                ctx=ctx,
                module_name="goal_planning",
                message=(
                    "Before I can project your goals on real numbers, I need a few "
                    f"more details: {needed}. Open Goal Planning to add them and "
                    "I'll run the projection right away."
                ),
            )
            return ChatHandlerResult(text=text)
        raise

    text = await format_with_telemetry(
        ctx=ctx,
        facts_pack=outcome.facts_pack,
        body_prompt=_BODY_PROMPT,
        module_name="goal_planning",
        action_mode="narrate",
        profile={"first_name": getattr(ctx.user_ctx, "first_name", None)},
        build_fallback=lambda: outcome.fallback_text,
    )

    chart_payloads = _build_cashflow_chart_payloads(outcome.snapshot)
    return ChatHandlerResult(text=text, chart_payloads=chart_payloads)
