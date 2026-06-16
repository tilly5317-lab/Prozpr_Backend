"""Run the practical (holdings-aware) goal-based allocation pipeline.

Thin wrapper around ``AI_Agents/src/practical_asset_allocation``. The practical
pipeline is pure-Python (no LLM rationale step), so — unlike asset_allocation —
there is no API-key resolution here: build input, run on a worker thread,
return the structured output.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.domains.ai_engine.common import ensure_ai_agents_path, trace_line
from app.domains.practical_asset_allocation.services.paa_engine.input_builder import (
    build_practical_allocation_input_for_user,
)

if TYPE_CHECKING:
    from app.domains.ai_engine.turn_context import TurnContext

ensure_ai_agents_path()

from practical_asset_allocation.pipeline import (  # type: ignore[import-not-found]  # noqa: E402
    InfeasibleGoalError,
    PracticalAllocationOutput,
    run_practical_allocation,
)

logger = logging.getLogger(__name__)

_MSG_ENGINE_ERROR = (
    "We hit a quick snag while calculating your practical allocation — please "
    "try again in a moment. If it keeps happening, reach out via the help "
    "option and we'll take a look."
)


@dataclass(frozen=True)
class PracticalAllocationRunOutcome:
    """Immutable outcome of one practical-allocation pipeline run."""

    result: PracticalAllocationOutput | None
    blocking_message: str | None = None


async def compute_practical_allocation_result(
    user,
    user_question: str,
    *,
    chat_ctx: "TurnContext",
) -> PracticalAllocationRunOutcome:
    """Build inputs, run the practical pipeline, and return the outcome.

    Tolerant of partial profiles — the underlying asset_allocation input
    builder degrades missing fields to documented defaults, so the engine can
    always produce an answer.
    """
    trace_line("module: practical_asset_allocation — building inputs")

    try:
        practical_input, build_debug = build_practical_allocation_input_for_user(
            chat_ctx,
        )
    except Exception as exc:
        logger.exception("practical_asset_allocation input build failed: %s", exc)
        return PracticalAllocationRunOutcome(
            result=None,
            blocking_message=_MSG_ENGINE_ERROR,
        )

    trace_line(
        f"practical input: corpus={practical_input.total_corpus}, "
        f"mf={practical_input.mf_corpus}, stocks={practical_input.non_mf_equity_corpus}, "
        f"elss={practical_input.elss_corpus}, goals={len(practical_input.goals)}"
    )

    try:
        output = await asyncio.to_thread(run_practical_allocation, practical_input)
    except InfeasibleGoalError as exc:
        logger.warning("practical_asset_allocation infeasible: %s", exc)
        return PracticalAllocationRunOutcome(
            result=None,
            blocking_message=_MSG_ENGINE_ERROR,
        )
    except Exception as exc:
        logger.exception("practical_asset_allocation pipeline failed: %s", exc)
        trace_line(f"practical_asset_allocation ERROR: {exc!s}")
        return PracticalAllocationRunOutcome(
            result=None,
            blocking_message=_MSG_ENGINE_ERROR,
        )

    trace_line(f"PracticalAllocationOutput grand_total={output.grand_total}")
    return PracticalAllocationRunOutcome(result=output)


# ---------------------------------------------------------------------------
# Chat formatting
# ---------------------------------------------------------------------------

_BUCKET_ORDER = ["emergency", "short_term", "medium_term", "long_term"]
_BUCKET_TITLES = {
    "emergency": "Emergency",
    "short_term": "Short-term",
    "medium_term": "Medium-term",
    "long_term": "Long-term",
}


def build_practical_fallback_brief(output: PracticalAllocationOutput) -> str:
    """Render a ``PracticalAllocationOutput`` as user-facing markdown.

    Shares the headline-mix + per-bucket shape with the asset_allocation brief
    (the seven fields are identical), plus a practical-only corpus-split line
    (MF / non-MF equity / ELSS) drawn from ``corpus_breakdown``.
    """
    cs = output.client_summary
    lines: list[str] = []

    lines.append(
        f"Here is a **practical goal-based allocation** using your effective "
        f"risk score {cs.effective_risk_score:.1f} "
        f"(corpus INR {output.grand_total:,.0f})."
    )
    lines.append("")

    buckets_by_name = {b.bucket: b for b in output.bucket_allocations}
    for name in _BUCKET_ORDER:
        b = buckets_by_name.get(name)
        if b is None or b.allocated_amount <= 0:
            continue
        lines.append(
            f"**{_BUCKET_TITLES[name]} — INR {b.allocated_amount:,.0f}** "
            f"(goal need INR {b.total_goal_amount:,.0f})"
        )
    lines.append("")

    rec = output.asset_class_breakdown.recommended
    lines.append(
        f"**Recommended mix** — equity {rec.equity_total_pct:.1f}% "
        f"(INR {rec.equity_total:,.0f}), debt {rec.debt_total_pct:.1f}% "
        f"(INR {rec.debt_total:,.0f}), others {rec.others_total_pct:.1f}% "
        f"(INR {rec.others_total:,.0f})."
    )

    cb = output.corpus_breakdown
    lines.append("")
    lines.append(
        f"**Corpus split** — MF INR {cb.mf_corpus_inr:,.0f}, non-MF equity "
        f"INR {cb.non_mf_equity_input_inr:,.0f}, ELSS INR {cb.elss_corpus_inr:,.0f}."
    )

    return "\n".join(lines).rstrip() + "\n"
