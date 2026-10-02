"""Build a ``practical_asset_allocation.PracticalAllocationInput`` from a User.

``PracticalAllocationInput`` extends asset_allocation's ``AllocationInput`` with
holdings-aware corpus scalars. We reuse the asset_allocation input builder
for every shared profile / goal / risk field (so there's one source of truth for
that mapping), then add the practical-only scalars.

New scalars — no app-side data source wired yet, so they take safe defaults:
  elss_corpus          = 0.0           (ELSS MF subset, SEBI-locked)

``short_term_holdings`` (an AllocationInput field: held debt and arbitrage funds,
not income-plus-arbitrage) is read from the preloaded user's holdings — None when
none are on file — unless the caller passes it.

Entry: ``build_practical_allocation_input_for_user(ctx)`` →
``(PracticalAllocationInput, debug)``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.asset_allocation.services.aa_engine.input_builder import (
    build_goal_allocation_input_for_user,
)
from app.domains.mutual_funds.services.scheme_classification import (
    short_term_holdings_total,
)
from app.domains.portfolio.services.holdings_snapshot import snapshot_from_holdings

if TYPE_CHECKING:
    from app.domains.ai_engine.turn_context import TurnContext
    from practical_asset_allocation.human_override import HumanOverridePreferences

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
    elss_corpus: float


def load_human_override_for_user(user: Any) -> "HumanOverridePreferences | None":
    """Saved-row → ``HumanOverridePreferences`` mapping (S1 spec §4.3).

    The single source of truth for turning a ``SavedInvestmentPreference``
    row (read off the preloaded ``User.saved_investment_preference``
    relationship — no DB access here) into the pure engine-side preference
    model. Returns ``None`` when the user has no row, or the row carries no
    settable fields. Reused by the PAA input builder (merges a one-off on
    top) and by the asset_allocation service (ideal-parity, no one-off).
    """
    from practical_asset_allocation.human_override import HumanOverridePreferences

    saved = getattr(user, "saved_investment_preference", None)
    if saved is None:
        return None
    saved_fields = {
        "asset_class_requested": saved.asset_class_requested,
        "subgroup_emphasis": saved.resolved_targets or {},
    }
    merged = {k: v for k, v in saved_fields.items() if v not in (None, [], {})}
    if not merged:
        return None
    return HumanOverridePreferences(**merged)


def short_term_holdings_for_user(user: Any) -> float | None:
    """Held short-term money from the user's preloaded holdings; None when they
    have no holdings on file."""
    holdings = [
        h
        for p in (getattr(user, "portfolios", None) or [])
        for h in (getattr(p, "holdings", None) or [])
    ]
    snapshot = snapshot_from_holdings(holdings)
    if snapshot.total_inr <= 0:
        return None
    return short_term_holdings_total(snapshot.by_subgroup)


def build_practical_allocation_input_for_user(
    ctx: "TurnContext",
    corpus_pin: CorpusPin | None = None,
    apply_saved_preferences: bool = True,
    monthly_sip: float | None = None,
    short_term_holdings: float | None = None,
) -> tuple[PracticalAllocationInput, Dict[str, Any]]:
    """Return ``(PracticalAllocationInput, debug)`` for the User in ``ctx``."""
    base_input, debug = build_goal_allocation_input_for_user(ctx)

    shared = {k: getattr(base_input, k) for k in AllocationInput.model_fields}
    if corpus_pin is not None:
        shared["total_corpus"] = corpus_pin.total_corpus

    shared["short_term_holdings"] = (
        short_term_holdings
        if short_term_holdings is not None
        else short_term_holdings_for_user(getattr(ctx, "user_ctx", None))
    )
    if monthly_sip is not None:
        shared["monthly_sip"] = monthly_sip

    # ── The SINGLE preference load point (S1 spec §4.3). Engines stay DB-free:
    # the row rides the preloaded User relationship; a per-turn override dict
    # merges field-level over it (precedence: one-off > saved > none).
    from practical_asset_allocation.human_override import HumanOverridePreferences
    from app.domains.asset_allocation.services.aa_engine.overrides import (
        effective_param,
    )

    human_override = None
    if apply_saved_preferences and ctx is not None:
        saved_prefs = load_human_override_for_user(ctx.user_ctx)
        saved_fields = saved_prefs.model_dump() if saved_prefs is not None else {}
        one_off = effective_param(ctx, "human_override_preferences", None)
        merged = {
            k: v
            for k, v in {**saved_fields, **(one_off or {})}.items()
            if v not in (None, [], {})
        }
        if merged:
            human_override = HumanOverridePreferences(**merged)

    practical_input = PracticalAllocationInput(
        # Every shared AllocationInput field, verbatim (total_corpus included —
        # overridden above when a CorpusPin is supplied).
        **shared,
        # Default source is the profile (ELSS 0); a CorpusPin supplies
        # holdings-derived values (deficit-fill lumpsum path).
        elss_corpus=(corpus_pin.elss_corpus if corpus_pin else 0.0),
        human_override=human_override,
    )

    debug = {
        **debug,
        "corpus_pinned": corpus_pin is not None,
        "total_corpus": practical_input.total_corpus,
        "elss_corpus": practical_input.elss_corpus,
        "short_term_holdings": practical_input.short_term_holdings,
        "monthly_sip": practical_input.monthly_sip,
        "human_override_set": human_override is not None,
    }
    return practical_input, debug
