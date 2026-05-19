"""Chat handler for the GOAL_PLANNING intent.

Runs the input builder + agent service for the turn's User, then hands the
resulting ``facts_pack`` to the shared answer-formatter. The formatter LLM
is the customer-facing voice — this module never templates user-visible
prose itself; it produces facts and lets the formatter speak.
"""
from __future__ import annotations

import logging
from datetime import date

from app.services.ai_bridge.answer_formatter import format_with_telemetry
from app.services.ai_bridge.chat_dispatcher import ChatHandlerResult, register
from app.services.ai_bridge.goal_planning.service import (
    compute_goal_planning_snapshot,
)
from app.services.chat_core.turn_context import TurnContext

logger = logging.getLogger(__name__)


_BODY_PROMPT = """\
You are answering a customer question about their long-term financial plan,
using a pre-computed goal-planning snapshot produced by Prozpr's deterministic
engine (which the customer cannot see). The FACTS_PACK gives you every
quotable number.

How to read the FACTS_PACK:
- `headline` — top-line numbers about the plan (corpus today, projected
  closing corpus, total shortfall, years to last goal). All rupee values
  have a matching `_indian` sibling string.
- `retirement` — retirement date, years to retirement, corpus needed at
  retirement, today's-rupee equivalent.
- `cashflow_horizon` — totals across the projection (investments, returns,
  one-offs, goal payouts) so you can describe how money flows.
- `goals` — per-goal verdict (`funded` | `partially_funded` | `unfunded`),
  the headline amount (already Indian-notation), and a one-sentence note.
  Use the verdict to colour the language ("on track" vs "short by …").
- `narrative` — when present, has `top_line` (a vetted one-line summary
  you may quote), `retirement_note`, `cashflow_note`, and `risks` (a
  vetted bullet list of concerns). When `narrative` is `null` the
  summary step failed; lean on `headline`, `goals`, and `next_steps`
  instead, and keep the answer factual.
- `next_steps` — DETERMINISTIC action proposals from the engine's lever
  search. Each was proved feasible against the engine. Quote them verbatim
  when the customer asks "what should I do?" — do NOT invent new actions
  or extrapolate beyond what's in this list.
- `validation_issues` — data the customer hasn't shared yet. If the topic
  the customer asked about depends on this missing data, mention it gently
  ("we're using a default tax rate; update your profile for a sharper
  number") rather than pretending the projection is fully tailored.

Voice and length:
- This is a financial plan, not a portfolio query — speak to long-term
  goals, life events, retirement security. Avoid trading/fund language.
- Default length: 3–6 sentences for status questions; longer (with a goals
  table) only when the customer asks for a full plan review.
- Use a table when listing 2+ goals with their amounts/verdicts; bullets
  when listing 2+ risks or next steps.
- If `next_steps` is empty, do not invent suggestions — say the plan is on
  track or that you don't have enough information to recommend an action.
"""


@register("goal_planning")
async def goal_planning_chat(ctx: TurnContext) -> ChatHandlerResult:
    """Single chat handler — runs the agent, formats the reply."""
    try:
        outcome = await compute_goal_planning_snapshot(
            user=ctx.user_ctx,
            user_question=ctx.user_question,
            chat_session_id=str(ctx.session_id),
            anchor_date=date.today(),
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
    return ChatHandlerResult(text=text)
