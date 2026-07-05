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


from dataclasses import dataclass


@dataclass(frozen=True)
class CorpusPin:
    """Explicit corpus override for the practical allocation (deficit-fill path).

    Pins the ideal to actual holdings + fresh money instead of the
    profile-declared corpus (spec 2026-07-03, additional_investment lumpsum).
    Threaded as an explicit parameter — NOT the additional_cash_inr
    chat-override — so the what-if key never leaks into the normal flow."""

    total_corpus: float
    mf_corpus: float
    non_mf_equity_corpus: float
    elss_corpus: float


def build_practical_allocation_input_for_user(
    ctx: "TurnContext",
    corpus_pin: CorpusPin | None = None,
) -> tuple[PracticalAllocationInput, Dict[str, Any]]:
    """Return ``(PracticalAllocationInput, debug)`` for the User in ``ctx``."""
    base_input, debug = build_goal_allocation_input_for_user(ctx)

    shared = {k: getattr(base_input, k) for k in AllocationInput.model_fields}
    if corpus_pin is not None:
        shared["total_corpus"] = corpus_pin.total_corpus

    practical_input = PracticalAllocationInput(
        # Every shared AllocationInput field, verbatim (total_corpus included —
        # overridden above when a CorpusPin is supplied).
        **shared,
        # Practical-only scalars. Default source is the profile (stocks/ELSS 0 →
        # whole corpus treated as MF); a CorpusPin supplies holdings-derived
        # values for all four (deficit-fill lumpsum path).
        mf_corpus=(corpus_pin.mf_corpus if corpus_pin else base_input.total_corpus),
        non_mf_equity_corpus=(corpus_pin.non_mf_equity_corpus if corpus_pin else 0.0),
        elss_corpus=(corpus_pin.elss_corpus if corpus_pin else 0.0),
        max_non_mf_equity_pct_client_input=None,
    )

    debug = {
        **debug,
        "corpus_pinned": corpus_pin is not None,
        "total_corpus": practical_input.total_corpus,
        "mf_corpus": practical_input.mf_corpus,
        "non_mf_equity_corpus": practical_input.non_mf_equity_corpus,
        "elss_corpus": practical_input.elss_corpus,
    }
    return practical_input, debug
