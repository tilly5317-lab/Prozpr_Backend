"""Chat handler for the GOAL_PLANNING intent.

Runs the input builder + cashflow engine for the turn's User, then hands the
resulting ``facts_pack`` to the shared answer-formatter. The formatter LLM
is the customer-facing voice — this module never templates user-visible
prose itself; it produces facts and lets the formatter speak.
"""
from __future__ import annotations

import logging
from datetime import date

from app.domains.ai_engine.answer_formatter import format_with_telemetry
from app.domains.ai_engine.chat_dispatcher import ChatHandlerResult, register
from app.domains.cashflow.services.goal_planning_engine.service import (
    compute_goal_planning_snapshot,
)
from app.domains.ai_engine.turn_context import TurnContext

logger = logging.getLogger(__name__)


_BODY_PROMPT = """\
You are presenting a FULL cashflow statement / financial plan to the customer,
using a pre-computed goal-planning snapshot produced by Prozpr's deterministic
cashflow engine. The FACTS_PACK gives you every quotable number. Always show
the COMPLETE data in a structured, readable format.

MANDATORY SECTIONS TO INCLUDE (use all data from FACTS_PACK):

1. **Headline Summary** — use `headline` data:
   Show corpus today, total required, surplus/shortfall, years to last goal,
   number of goals, is_feasible verdict.

2. **Goals Funding Table** — use `goals` array:
   Show a markdown table with columns: Goal | Type | Target Date | Required (FV) | Funded | Shortfall | Status
   Include ALL goals.

3. **Retirement Snapshot** — use `retirement` data:
   Retirement date, years to retirement, corpus required (FV and PV today),
   annual household expense, post-retirement years.

4. **Fund Flow Summary** — use `cashflow_horizon` data:
   Show as a table/list: corpus opening, + total investments, + total ROI,
   + one-off inflows, - one-off outflows, - goal payouts, = corpus closing.

5. **Annual Cashflow Table** — use `annual_cashflow` array:
   Show a markdown table with columns: FY | Income | Tax | Expenses | Savings | Investment | Returns | Goal Payout | Corpus Closing | Funded?
   Show ALL years. Use the Indian-notation values provided.

6. **Narrative & Next Steps** — use `narrative` and `next_steps`:
   If narrative is present, include top_line, retirement_note, cashflow_note.
   List risks as bullets. List next_steps as numbered actions.

Formatting rules:
- Use `_indian` values for all rupee amounts (they are pre-formatted).
- Use markdown tables (pipe-separated).
- Bold key numbers and verdicts.
- If `narrative` is null, skip that section and present only the data tables.
- Do NOT invent numbers — only use what's in FACTS_PACK.
- Greet the customer by first_name if available.
"""


def _build_cashflow_chart_payloads(snapshot) -> list[dict]:
    """Build chart_payloads for the frontend from the cashflow snapshot."""
    annual = snapshot.annual_cashflow
    if not annual:
        return []

    return [{
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
    }]


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
            return ChatHandlerResult(
                text=(
                    "To run a goal projection for you, I'll need your date of "
                    "birth — it anchors the math. Add it in settings, and "
                    "we'll pick this up right away."
                ),
            )
        if str(e) == "missing_financial_profile":
            return ChatHandlerResult(
                text=(
                    "I need your financial profile to run a cashflow projection — "
                    "things like annual income, expenses, and current assets. "
                    "Please update your profile and we'll get this done."
                ),
            )
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
