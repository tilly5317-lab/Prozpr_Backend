"""practical_asset_allocation pipeline — see module __init__ docstring."""
from __future__ import annotations

# Stub: full implementation lands across Tasks 2–12. Placeholder symbols below
# satisfy the __init__.py re-exports during early TDD iterations.


class InfeasibleGoalError(ValueError):
    """Raised when the input corpus cannot satisfy structural constraints
    (e.g. ELSS holdings exceed total corpus)."""


# These names are filled in by later tasks. Keep the import surface stable.
class PracticalAllocationInput:  # type: ignore[no-redef]
    pass


class CorpusBreakdown:  # type: ignore[no-redef]
    pass


class PracticalAllocationOutput:  # type: ignore[no-redef]
    pass


def run_practical_allocation(inp):  # type: ignore[no-redef]
    raise NotImplementedError(
        "run_practical_allocation: implementation lands in Tasks 4-12 of "
        "docs/superpowers/plans/2026-05-23-allocation-rebalancing-v2-part-b-*-plan.md"
    )
