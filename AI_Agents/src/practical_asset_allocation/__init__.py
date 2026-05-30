"""practical_asset_allocation — holdings-aware goal-based allocation.

Wraps asset_allocation_pydantic (steps 1-3 imported verbatim) and reimplements
the long-term step with ELSS freeze, non-MF equity NFA-banded cap, and the v2
average-based equity-subgroup sliding threshold.

Per spec §B.1 this is the first explicit cross-agent import under
AI_Agents/src/; see CLAUDE.md for the dependency edge documentation.
"""
from .pipeline import (
    CorpusBreakdown,
    InfeasibleGoalError,
    PracticalAllocationInput,
    PracticalAllocationOutput,
    run_practical_allocation,
)

__all__ = [
    "CorpusBreakdown",
    "InfeasibleGoalError",
    "PracticalAllocationInput",
    "PracticalAllocationOutput",
    "run_practical_allocation",
]
