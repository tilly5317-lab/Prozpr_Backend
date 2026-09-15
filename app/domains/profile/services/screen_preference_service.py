"""S4 percentage-screen preferences — the translation layer.

The redesigned screen speaks percentages of the WHOLE portfolio: an explicit
Equity / Debt / Commodity (`others`) split plus optional subcategory pins. This
module turns that into the exact shape the allocation engine already consumes
(`ResolvedPreferences`: `asset_class_requested` + `subgroup_emphasis`, both a
share of the whole portfolio since D-A3), and reads the saved row + a neutral
run back out for the screen. No engine or DB change — see
`docs/superpowers/specs/2026-09-10-investment-preferences-s4-pct-screen-backend-design.md`.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from app.domains.additional_investment.services.lumpsum_reasoning import subgroup_label
from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.mutual_funds.services.investment_preferences import ResolvedPreferences
from app.domains.profile.schemas import (
    ScreenPreferenceGetResponse,
    ScreenSaved,
    ScreenSaveResponse,
    ScreenSubcategory,
)
from app.domains.profile.services.preference_save_service import (
    _build_ctx,
    _eager_refresh,
    _persist_confirm,
    _run_preferred,
    active_preference_row,
)

ensure_ai_agents_path()
from asset_allocation_pydantic.tables import (  # noqa: E402
    DEFAULT_MULTI_ASSET_COMPOSITION_PCTS,
)
from practical_asset_allocation.human_override import (  # noqa: E402
    CLASS_OF,
    FROZEN_SUBGROUPS,
    SETTABLE_SUBGROUPS,
)

_CLASSES = ("equity", "debt", "others")
_SUM_TOLERANCE = 0.5
# Every sub-group is settable except the frozen HOLDINGS rows (ELSS, direct
# stock) — the sleeve included (D-A2, 2026-09-14): a multi_asset pin sizes the
# multi-asset fund, and a zero empties it.
_SETTABLE_IDS = frozenset(sg for sg in SETTABLE_SUBGROUPS if sg not in FROZEN_SUBGROUPS)
# Spec 2026-09-15 §5. Read off the engine's own composition rather than written
# out, so the class-fit check and the engine's carve cannot drift apart.
_MULTI_ASSET_SLICES: dict[str, float] = dict(
    zip(_CLASSES, (pct / 100.0 for pct in DEFAULT_MULTI_ASSET_COMPOSITION_PCTS))
)


def _class_budget_consumed(subgroup: str, pct_of_total: float) -> dict[str, float]:
    """How much of each class bar a pin spends — spec 2026-09-15 §5.

    `multi_asset` is ONE fund holding all three classes, and the engine carves
    it by `multi_asset_composition`, so a 20% ask spends 13 equity, 5 debt and
    2 others. `CLASS_OF` files it flatly under equity, which charged the whole
    20 against the equity bar — enough to reject the screen's own
    recommendation, whose sleeve is typically half the portfolio. Every other
    row spends its whole share in its own class.
    """
    if subgroup == "multi_asset":
        return {cls: pct_of_total * share for cls, share in _MULTI_ASSET_SLICES.items()}
    return {CLASS_OF.get(subgroup, "others"): pct_of_total}


class ScreenPreferenceError(ValueError):
    """Invalid screen preference payload — surfaced to the customer as HTTP 422."""


def resolve_screen_preferences(
    class_mix: dict[str, float], pins: list[dict]
) -> ResolvedPreferences:
    """Explicit `{class_mix}` (% of total) + `pins` (% of total) → `ResolvedPreferences`.

    Both pass straight through: the engine speaks % of the whole portfolio too
    (D-A3), so there is nothing left to convert. A pin is still checked against
    its class share — the class split is the outer truth its class must honour.

    Pins arrive COMPLETE (spec 2026-09-15): one entry per settable category,
    blanks sent as explicit zeros. A zero is a valid entry — the engine reads 0
    in `subgroup_emphasis` as a hard exclusion, which is exactly what an emptied
    row means. An OMITTED row is a different fact and still means "engine
    decides", so the two must not be collapsed.
    """
    mix = {c: float(class_mix.get(c, 0.0)) for c in _CLASSES}
    total = sum(mix.values())
    if abs(total - 100.0) > _SUM_TOLERANCE:
        raise ScreenPreferenceError(
            f"Equity + Debt + Commodity must total 100% (got {total:.0f}%)."
        )

    emphasis: dict[str, float] = {}
    pinned_of_total: dict[str, float] = {c: 0.0 for c in _CLASSES}
    for pin in pins:
        sg = pin["subgroup"]
        if sg not in _SETTABLE_IDS:
            raise ScreenPreferenceError(f"{sg} is not a settable category.")
        pot = float(pin["pct_of_total"])
        if pot < 0:
            raise ScreenPreferenceError(f"{sg}: a share cannot be negative.")
        # A zero spends nothing, so it can never push a class over its bar.
        for cls, spent in _class_budget_consumed(sg, pot).items():
            pinned_of_total[cls] += spent
            if pinned_of_total[cls] > mix[cls] + _SUM_TOLERANCE:
                raise ScreenPreferenceError(
                    f"Pinned categories inside {cls} exceed its {mix[cls]:.0f}% share."
                )
        emphasis[sg] = pot

    return ResolvedPreferences(
        asset_class_requested=mix,
        subgroup_emphasis=emphasis,
        applied_defaults={},  # screen sends explicit numbers — nothing to disclose
    )


# ---------------------------------------------------------------------------
# Read model — recommendation + settable-subcategory catalog
# ---------------------------------------------------------------------------


def _settable_subcategory_ids() -> list[str]:
    return sorted(_SETTABLE_IDS)


def subcategory_catalog(out) -> list[ScreenSubcategory]:
    """Settable subcategories + Prozpr's recommended share of total, from a
    neutral practical-allocation run output."""
    grand = float(getattr(out, "grand_total", 0.0)) or 1.0
    rec_by_sg = {r.subgroup: float(r.total) for r in out.aggregated_subgroups}
    items: list[ScreenSubcategory] = []
    for sg in _settable_subcategory_ids():
        items.append(
            ScreenSubcategory(
                id=sg,
                label=subgroup_label(sg),
                recommended_pct_of_total=round(rec_by_sg.get(sg, 0.0) * 100.0 / grand, 1),
                **{"class": CLASS_OF.get(sg, "others")},
            )
        )
    return items


async def screen_read_model(db, user) -> ScreenPreferenceGetResponse:
    """GET payload: the saved split, the class-level recommendation, and the
    subcategory catalog — from one neutral run. Everything is already % of
    total, so the saved row is echoed back as-is."""
    from app.domains.practical_asset_allocation.services.paa_engine.input_builder import (
        build_practical_allocation_input_for_user,
    )
    from practical_asset_allocation.pipeline import (
        carve_outs_at_risk,
        run_practical_allocation,
    )

    inp, _ = build_practical_allocation_input_for_user(
        _build_ctx(user), apply_saved_preferences=False
    )
    out = await asyncio.to_thread(run_practical_allocation, inp)
    rec = out.asset_class_breakdown.recommended
    class_rec = {
        "equity": rec.equity_total_pct,
        "debt": rec.debt_total_pct,
        "others": rec.others_total_pct,
    }

    row = await active_preference_row(db, user.id)
    saved: Optional[ScreenSaved] = None
    if row is not None and row.asset_class_requested is not None:
        mix = row.asset_class_requested
        pins: list[dict] = []
        # Zeros are PRESERVED (spec 2026-09-15 §6). A stored 0 is the customer
        # emptying that row; filtering it out returned the engine's number in
        # its place, so the screen showed them something they had not asked for.
        for sg, pct_of_total in (row.resolved_targets or {}).items():
            pins.append({"subgroup": sg, "pct_of_total": float(pct_of_total)})
        saved = ScreenSaved(class_mix=mix, pins=pins, saved_at=row.activated_at)

    return ScreenPreferenceGetResponse(
        saved=saved,
        recommendation={"class_mix": class_rec},
        subcategories=subcategory_catalog(out),
        # Spec 2026-09-15 §9.1. Read off the same profile the catalog was built
        # from, through the engine's own helper — so this warning and the §9
        # record attached after the run can never name different facts.
        carve_outs_at_risk=carve_outs_at_risk(inp),
    )


# ---------------------------------------------------------------------------
# Save orchestration
# ---------------------------------------------------------------------------


async def save_screen_preference(db, user, class_mix, pins) -> ScreenSaveResponse:
    """Translate the screen payload -> run the engine -> persist (immutable
    versioned row) -> refresh the standing plans. Reuses the existing save
    machinery entirely; no engine change."""
    resolved = resolve_screen_preferences(class_mix, pins)  # raises ScreenPreferenceError
    intent = {"class_mix": class_mix, "pins": pins}  # customer_choices (what they set)
    prior = await active_preference_row(db, user.id)
    if prior is not None and intent == (prior.customer_choices or None):
        return ScreenSaveResponse(ok=True, no_op=True)  # unchanged — skip the engine + persist

    preferred, blocking = await _run_preferred(user, resolved)
    if preferred is None:
        return ScreenSaveResponse(
            ok=False,
            blocked=blocking or "We couldn't build a plan right now - please try again.",
        )

    await _persist_confirm(db, user, resolved, intent, preferred, prior)
    await _eager_refresh(db, user)
    return ScreenSaveResponse(ok=True)
