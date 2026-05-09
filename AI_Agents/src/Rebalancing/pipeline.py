"""Pipeline orchestrator. Pure-sync, DB-free.

Composes the six steps in order, threads the warnings + unrebalanced
remainder from step 1 through to step 6, and returns the final response.
"""

from __future__ import annotations

from .models import RebalancingComputeRequest, RebalancingComputeResponse
from .steps import (
    step1_cap_and_spill,
    step2_compare_and_decide,
    step3_tax_classification,
    step4_initial_trades_under_stcg_cap,
    step5_loss_offset_top_up,
    step6_presentation,
)


def run_rebalancing(request: RebalancingComputeRequest) -> RebalancingComputeResponse:
    s1_rows, s1_warnings, unrebalanced_total = step1_cap_and_spill.apply(
        request.rows, request
    )
    s2_rows, s2_warnings = step2_compare_and_decide.apply(s1_rows, request)
    s3_rows = step3_tax_classification.apply(s2_rows, request)
    s4_rows, s4_warnings = step4_initial_trades_under_stcg_cap.apply(s3_rows, request)
    s5_rows = step5_loss_offset_top_up.apply(s4_rows, request)

    all_warnings = list(s1_warnings) + list(s2_warnings) + list(s4_warnings)
    return step6_presentation.apply(
        s5_rows, request, all_warnings, unrebalanced_total
    )
