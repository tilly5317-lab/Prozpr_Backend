"""Chat handler for the GOAL_PLANNING intent.

Runs the input builder + cashflow engine for the turn's User, then hands the
resulting ``facts_pack`` to the shared answer-formatter. The formatter LLM
is the customer-facing voice — this module never templates user-visible
prose itself; it produces facts and lets the formatter speak.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.domains.ai_engine.answer_formatter import (
    format_relay_or_canned,
    format_with_telemetry,
)
from app.domains.ai_engine.chat_dispatcher import ChatHandlerResult, register
from app.domains.ai_engine.classifier_llm import classify_action
from app.domains.ai_engine.common import build_detect_history_block
from app.domains.ai_engine.turn_context import TurnContext
from app.domains.cashflow.services.goal_planning_engine.service import (
    compute_goal_planning_snapshot,
)

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

When ACTION_MODE is `counterfactual_explore`, the CUSTOMER_RECORD you are given is the
plan RE-RUN with the customer's proposed change already applied — not their
current plan. Answer as "here is what that would look like", lead with whether it
still works, and name the change you modelled so they can see it was understood
("retiring at 50 instead of 55"). Do not present it as their existing plan, and
do not claim anything has been saved — nothing was.

The full year-by-year cashflow statement never belongs in chat, even when asked
for directly. It lives in the Goal Planning screen, and the app renders its chart
beside your reply. If the customer asks to see it, say where it lives, and answer
what they were actually after in a sentence or two — the trajectory, a turning
point, a specific year from `annual_cashflow`. Never render the table.

The CUSTOMER_RECORD has this shape (treat fields not present as unknown):

  headline — the plan-level verdict.
    is_feasible: bool — do all goals get funded on the current trajectory
    corpus_today / corpus_closing — corpus now, and at the end of the horizon.
      `corpus_today` is TOTAL INVESTABLE ASSETS: the linked mutual-fund portfolio
      PLUS directly-held equity PLUS cash and debt holdings. It is usually LARGER
      than the portfolio value the customer sees on screen, so never call it "their
      portfolio" — say "everything you hold" or "your total investments".
      `corpus_closing` is a year-end balance in the accounting sense; never describe
      a corpus as having "closed"

  corpus_composition — what `corpus_today` is made of: linked_portfolio_value (the
    mutual-fund portfolio they see in the app), direct_equity_shares, cash_and_debt.
    When the customer asks about "my portfolio" specifically, answer with
    linked_portfolio_value and name the rest separately — quoting the merged corpus
    as their portfolio overstates it. When they ask about their overall position,
    corpus_today is the right number.

    These are TODAY's figures only. The projection — annual_cashflow, every
    corpus_closing, corpus_today itself — models the COMBINED corpus and nothing
    else. There is no year-by-year forecast for the portfolio, the equity, or the
    cash on their own, so never project a component forward or name a future year
    for one. If asked when a component reaches a value, say the forecast covers
    their combined investments and answer on that basis.
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

  retirement — the retirement date and age the customer set IN THEIR PROFILE:
    planned_retirement_date_from_profile, planned_retirement_age_from_profile
    (quote it; never work an age out yourself, and never read a year as an age),
    years_to_planned_retirement, annual_household_expense_today,
    post_retirement_years.

    `is_funded_as_a_goal` says whether the plan funds retirement from this block.
    When it is false — the normal case — nothing here is funded: this is the
    projection horizon, and retirement is paid for solely by whatever the
    customer put in `goals[]`. corpus_required_* appear only when it is true.

    A goal named "Retirement" in `goals[]` is a SEPARATE thing with its own date
    and amounts, and those are what answer "how much do I need to retire?". The
    two dates can disagree — customers set a retirement age in their profile and
    then create a goal on a different date. If they disagree and it bears on the
    answer, say so plainly rather than picking one or splicing them together.

  cashflow_horizon — totals across the whole projection: corpus_opening,
    total_investments, total_roi, total_one_off_in, total_one_off_out,
    total_goals_paid, corpus_closing

  annual_cashflow[] — one row per financial year: fy_label, is_funded, and
    pre-formatted amounts income_indian, income_tax_indian,
    household_expense_indian, savings_pre_emi_indian, savings_post_emi_indian,
    monthly_investment_indian, investment_returns_indian, goal_payout_indian —
    quote these verbatim. `corpus_closing` additionally ships as a RAW number
    (with corpus_closing_indian for display) because it is the one figure you
    compare across years. Reference material for year-specific questions; not
    something to render wholesale.

    For "when will I reach ₹X?", compare the RAW `corpus_closing` numbers against
    X and name the fy_label of the FIRST year that reaches it — never the last row
    of the table, and never a later year's corpus. Quote the `_indian` sibling in
    the reply. If `headline.corpus_today` is already at or above X, the answer is
    that they are ALREADY there: lead with that and give today's figure. Do not
    name a future year, and never open with a year you then contradict.

  validation_issues — engine warnings worth raising if they bear on the question.

The verdict and the wording are yours to write from these numbers — nothing in
the pack is pre-written prose to lift.

Every rupee amount has a pre-formatted `_indian` sibling — use it verbatim; never
re-derive or convert lakh/crore yourself. Quote only what is in the CUSTOMER_RECORD.
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


class GoalChatAction(BaseModel):
    """What the customer is asking of their goal plan."""

    mode: Literal["narrate", "counterfactual_explore", "clarify"] = "narrate"
    overrides: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "For counterfactual_explore only. Allowed keys: retirement_age (int), "
            "starting_monthly_investment (rupees/month), "
            "monthly_household_expense (rupees/month), "
            "one_off_outflow ({description, amount, date as YYYY-MM-DD — resolve "
            "'next year'/'in 3 years' against TODAY, given in the user block; the "
            "date must be in the future})."
        ),
    )
    clarification_question: Optional[str] = Field(
        default=None,
        description="When mode='clarify', the one question to ask the customer.",
    )


_DETECT_SYSTEM = """\
You route a customer's question about their own goal plan. Choose ONE mode.

Judge the customer's CURRENT question and nothing else. The history is background
for resolving shorthand ("what about 50?" after "what if I retire early?") — it is
NEVER a source of overrides. A change the customer mentioned in an earlier turn has
already been dealt with; re-applying it now would answer a question nobody asked. If
the current question proposes no change of its own, the mode is narrate.

narrate — the default, and the right answer for most questions. Use it whenever
  the customer is asking about the plan AS IT STANDS: "will I meet my goals?",
  "how much do I need for retirement?", "am I on track?", "why is there a
  shortfall?", "how did you work that out?". Nothing is being changed.

counterfactual_explore — the customer proposes a CHANGE and wants to know its
  effect. The change must map onto one of the allowed override keys:
    "what if I retire at 50?"                  -> retirement_age: 50
    "what if I invest 50k more a month?"       -> starting_monthly_investment:
                                                  <current + 50000>
    "what if my expenses go to 3 lakh?"        -> monthly_household_expense: 300000
    "can I afford a 10 lakh trip next June?"   -> one_off_outflow:
                                                  {description, amount, date}
  Convert Indian units yourself: 1 lakh = 100000, 1 crore = 10000000. For a
  RELATIVE change ("50k more"), add it to the current value given below.

  A feasibility question ("will I…", "can I…", "am I able to…") IS proposing a
  change when it names an override value that DIFFERS from the current plan
  inputs above — the named value is the change, so route to counterfactual_explore,
  not narrate. Compare against the plan's retirement age given above:
    "will my SIP get me there by 55?"   (plan retires at 60) -> retirement_age: 55
    "can I retire at 55?"               (plan retires at 60) -> retirement_age: 55
    "can I hit FI five years early?"    (plan retires at 60) -> retirement_age: 55
  Only route to narrate when the feasibility question names NO new value, or the
  named value equals the plan's — then nothing is being changed ("will I meet my
  goals?" -> narrate).

clarify — the customer clearly wants a counterfactual but a required number is
  missing or ambiguous ("what if I retire earlier?" — earlier than what?). Ask
  exactly one short question. Do NOT use clarify to avoid a hard question; if
  the plan as it stands can answer it, narrate.

If a proposed change does not map onto an allowed key (a different return
assumption, a new goal, changing inflation), choose narrate — the plan as it
stands is still the honest answer, and inventing an unsupported override would
produce a confident, wrong projection.
"""


_DEFAULT_CLARIFY_FALLBACK = (
    "Could you share a bit more — e.g., the age you'd like to retire at, a "
    "monthly investment amount, or the goal you have in mind?"
)


async def _detect_goal_action(ctx: TurnContext) -> GoalChatAction:
    """One Haiku call: what is being asked of the plan? Never writes prose."""
    # Read through the same resolver the input builder uses — the raw ORM columns
    # do not carry these directly (personal_finance_profile.monthly_investment is
    # None for users whose SIP is derived), so a naive getattr would leave the
    # detector unable to compute a relative change like "₹50k more a month".
    from app.domains.profile.services.profile_finance import personal_finance_scalars

    scalars = personal_finance_scalars(ctx.user_ctx)
    current = {
        "monthly_household_expense": scalars.get("monthly_household_expense"),
        "starting_monthly_investment": scalars.get("starting_monthly_investment"),
        "retirement_age": getattr(
            getattr(ctx.user_ctx, "investment_profile", None), "retirement_age", None
        ),
    }
    history_block = build_detect_history_block(ctx.conversation_history)
    user_block = (
        f"TODAY: {date.today().isoformat()}\n\n"
        + (
            "BACKGROUND — earlier turns, for resolving shorthand ONLY. Never take an\n"
            "override from here:\n" + history_block + "\n\n"
            if history_block
            else ""
        )
        + f"Current plan inputs (for relative changes): {current}\n\n"
        + f"THE QUESTION TO JUDGE: {ctx.user_question}"
    )
    return await classify_action(
        action_model=GoalChatAction,
        system_prompt=_DETECT_SYSTEM,
        user_block=user_block,
        api_key=get_settings().get_anthropic_goal_planning_key(),
        max_tokens=300,
    )


@register("goal_planning")
async def goal_planning_chat(ctx: TurnContext) -> ChatHandlerResult:
    """Detect what's being asked, run the engine, format the reply."""
    try:
        action = await _detect_goal_action(ctx)
    except Exception:
        # A detector failure must never cost the turn — fall back to the plan
        # as it stands, which is what every goal turn did before detection.
        logger.warning("goal detect failed; falling back to narrate", exc_info=True)
        action = GoalChatAction(mode="narrate")

    if action.mode == "clarify":
        # Bypasses the formatter, exactly as asset_allocation and rebalancing do.
        # The fallback matters: without it an empty question fell through and we
        # computed a full projection for a turn the detector said to ask about.
        text = action.clarification_question or _DEFAULT_CLARIFY_FALLBACK
        return ChatHandlerResult(text=text)

    overrides = action.overrides if action.mode == "counterfactual_explore" else None

    try:
        outcome = await compute_goal_planning_snapshot(
            user=ctx.user_ctx,
            user_question=ctx.user_question,
            chat_session_id=str(ctx.session_id),
            anchor_date=date.today(),
            db=ctx.db,
            overrides=overrides,
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
        action_mode=action.mode,
        profile={"first_name": getattr(ctx.user_ctx, "first_name", None)},
        build_fallback=lambda: outcome.fallback_text,
    )

    chart_payloads = _build_cashflow_chart_payloads(outcome.snapshot)
    return ChatHandlerResult(text=text, chart_payloads=chart_payloads)
