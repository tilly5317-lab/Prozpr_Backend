"""Build a ``practical_asset_allocation.PracticalAllocationInput`` from a User.

``PracticalAllocationInput`` extends asset_allocation's ``AllocationInput`` with
four holdings-aware corpus scalars. We reuse the asset_allocation input builder
for every shared profile / goal / risk field (so there's one source of truth for
that mapping), then add the practical-only scalars.

New scalars — no app-side data source wired yet, so they take safe defaults:
  mf_corpus            = total_corpus  (all corpus treated as MF holdings)
  non_mf_equity_corpus = 0.0           (direct stocks / PMS — "stocks")
  elss_corpus          = 0.0           (ELSS MF subset, SEBI-locked)
  max_non_mf_equity_pct_client_input = None  (no advisor override)

Entry: ``build_practical_allocation_input_for_user(ctx)`` →
``(PracticalAllocationInput, debug)``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.asset_allocation.services.aa_engine.input_builder import (
    build_goal_allocation_input_for_user,
)

if TYPE_CHECKING:
    from app.domains.ai_engine.turn_context import TurnContext

ensure_ai_agents_path()

from asset_allocation_pydantic.models import (  # type: ignore[import-not-found]  # noqa: E402
    AllocationInput,
)
from practical_asset_allocation.pipeline import (  # type: ignore[import-not-found]  # noqa: E402
    PracticalAllocationInput,
)


def build_practical_allocation_input_for_user(
    ctx: "TurnContext",
) -> tuple[PracticalAllocationInput, Dict[str, Any]]:
    """Return ``(PracticalAllocationInput, debug)`` for the User in ``ctx``."""
    base_input, debug = build_goal_allocation_input_for_user(ctx)

    practical_input = PracticalAllocationInput(
        # Every shared AllocationInput field, verbatim (total_corpus included).
        **{k: getattr(base_input, k) for k in AllocationInput.model_fields},
        # Practical-only scalars. No data source yet → defaults; "stocks"
        # (non-MF equity) and ELSS are 0, so the whole corpus is treated as MF.
        mf_corpus=base_input.total_corpus,
        non_mf_equity_corpus=0.0,
        elss_corpus=0.0,
        max_non_mf_equity_pct_client_input=None,
    )

    debug = {
        **debug,
        "mf_corpus": base_input.total_corpus,
        "non_mf_equity_corpus": 0.0,
        "elss_corpus": 0.0,
    }
    return practical_input, debug
