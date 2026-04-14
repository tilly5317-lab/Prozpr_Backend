"""Goal-based asset allocation pipeline.

Deprecated: use ``goal_based_allocation_pydantic`` instead. The pydantic
package replaces the LCEL/LLM pipeline with a deterministic implementation
that retains a single scoped LLM call only for rationale text.
"""

import warnings

warnings.warn(
    "goal_based_allocation is deprecated; use goal_based_allocation_pydantic "
    "(deterministic allocation with scoped Step 7 LLM rationale).",
    DeprecationWarning,
    stacklevel=2,
)
