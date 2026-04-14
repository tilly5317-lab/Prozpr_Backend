from __future__ import annotations

from typing import Callable, Optional

from .models import AllocationInput, GoalAllocationOutput
from .steps import (
    step1_emergency,
    step2_short_term,
    step3_medium_term,
    step4_long_term,
    step5_aggregation,
    step6_guardrails,
    step7_presentation,
)
from .steps._rationale_llm import RationaleResponse


RationaleFn = Callable[..., RationaleResponse]


def run_allocation(
    inp: AllocationInput,
    rationale_fn: Optional[RationaleFn] = None,
) -> GoalAllocationOutput:
    s1 = step1_emergency.run(inp)
    s2 = step2_short_term.run(inp, s1.remaining_corpus)
    s3 = step3_medium_term.run(inp, s2.remaining_corpus)
    s4 = step4_long_term.run(inp, s3.remaining_corpus)
    s5 = step5_aggregation.run(inp.total_corpus, s1, s2, s3, s4)
    s6 = step6_guardrails.run(s4, s5, inp.effective_risk_score)
    return step7_presentation.run(
        inp, s1, s2, s3, s4, s5, s6, rationale_fn=rationale_fn
    )
