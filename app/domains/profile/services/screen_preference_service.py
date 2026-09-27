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
import logging
import math
from typing import Optional

from app.domains.additional_investment.services.ainv_engine.holdings_snapshot import (
    HoldingsSnapshot,
    load_holdings_snapshot,
)
from app.domains.additional_investment.services.lumpsum_reasoning import subgroup_label
from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.mutual_funds.services.investment_preferences import ResolvedPreferences
from app.domains.profile.schemas import (
    ScreenCurrent,
    ScreenCurrentHolding,
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
from asset_allocation_pydantic.equity_subgroup_slider import (  # noqa: E402
    apply_equity_subgroup_slider,
)
from asset_allocation_pydantic.steps.step4_long_term import (  # noqa: E402
    phase5_equity_subgroups,
)
from asset_allocation_pydantic.tables import (  # noqa: E402
    DEFAULT_MULTI_ASSET_COMPOSITION_PCTS,
)
from practical_asset_allocation.human_override import (  # noqa: E402
    CLASS_OF,
    FROZEN_SUBGROUPS,
    SETTABLE_SUBGROUPS,
)
from practical_asset_allocation.pipeline import DEBT_DEFAULT_ORDER  # noqa: E402

logger = logging.getLogger(__name__)

_CLASSES = ("equity", "debt", "others")
# A class as the customer reads it: the screen calls "others" Commodity.
_CLASS_WORD = {"equity": "equity", "debt": "debt", "others": "commodity"}
_SUM_TOLERANCE = 0.5
# Every sub-group is settable except the frozen HOLDINGS rows (ELSS, direct
# stock) — the sleeve included (D-A2, 2026-09-14): a multi_asset pin sizes the
# multi-asset fund, and a zero empties it.
_SETTABLE_IDS = frozenset(sg for sg in SETTABLE_SUBGROUPS if sg not in FROZEN_SUBGROUPS)
# Spec 2026-09-15 §5. Read off the engine's own composition rather than written
# out, so the class-fit check, the engine's carve and the figures the GET hands
# the screen cannot drift apart.
_MULTI_ASSET_COMPOSITION: dict[str, float] = dict(
    zip(_CLASSES, DEFAULT_MULTI_ASSET_COMPOSITION_PCTS)
)
# Only the engine's equity SHARES are kept, so the pool is nominal: ₹1cr, large
# enough that its ₹100 amount rounding is noise.
_NOMINAL_EQUITY_POOL = 10_000_000


def _js_round(value: float) -> int:
    """`Math.round` — half UP. Python's `round` is banker's (2.5 -> 2), so it
    would carve differently from the screen on every exact half."""
    return math.floor(value + 0.5)


def _class_budget_consumed(subgroup: str, pct_of_total: float) -> dict[str, float]:
    """How much of each class bar a pin spends — spec 2026-09-15 §5.

    `multi_asset` is ONE fund holding all three classes, and the engine carves
    it by `multi_asset_composition`, so a 20% ask spends 13 equity, 5 debt and
    2 others. `CLASS_OF` files it flatly under equity, which charged the whole
    20 against the equity bar — enough to reject the screen's own
    recommendation, whose sleeve is typically half the portfolio. Every other
    row spends its whole share in its own class.

    The sleeve is charged the screen's WHOLE-PERCENT carve, not the raw
    composition: debt and others rounded half up, equity the rest — which is
    what the screen shows, and what it fills each class's own rows up to. The
    check must charge the fund exactly that, or the screen's own balanced
    numbers are rejected: at the raw carve its equity rows overshoot by up to
    0.9, past the 0.5 tolerance, for about one sleeve size in five.
    """
    if subgroup == "multi_asset":
        comp = _MULTI_ASSET_COMPOSITION
        debt = _js_round(pct_of_total * comp["debt"] / 100)
        others = _js_round(pct_of_total * comp["others"] / 100)
        return {"equity": pct_of_total - debt - others, "debt": debt, "others": others}
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
        if sg == "multi_asset" and pot > 0:
            # Zero means zero. Any amount of the fund carries some of every
            # class it holds, which the whole-percent carve below can hide
            # (1% → 0.25% debt → 0) — but the engine builds no sleeve at all
            # without debt (`_sleeve_size`), so such a pin would be silently
            # dropped from the plan. Refuse it here instead.
            for cls in _CLASSES:
                if mix[cls] <= 0 and _MULTI_ASSET_COMPOSITION[cls] > 0:
                    word = _CLASS_WORD[cls]
                    raise ScreenPreferenceError(
                        f"Multi-asset funds hold some {word}, so they can't be part of a mix with 0% {word}."
                    )
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


def _engine_default_split(cls: str, rows: list[str], inp) -> dict[str, float]:
    """Where the engine itself sends a class's long-term money when no row of
    it is named — the split for a class the plan gave no money of its own.

    Equity: the ideal engine's own two calls, phase 5 then the slider, for this
    customer's score and market view (`pipeline.py` R196-R215). Debt: wholly to
    the first settable row of `DEBT_DEFAULT_ORDER` — never plain `arbitrage`,
    which the engine does not pick unasked (spec 2026-09-15 §4). Others: wholly
    to gold, where the engine leaves the commodity residual (R220-R222).
    """
    if cls == "equity":
        amounts = phase5_equity_subgroups(
            total_equity_for_subgroups=_NOMINAL_EQUITY_POOL,
            score=inp.effective_risk_score,
            market_commentary=inp.market_commentary,
        )
        amounts, _, _ = apply_equity_subgroup_slider(amounts, equity_pool=_NOMINAL_EQUITY_POOL)
        return {sg: float(amounts.get(sg, 0)) for sg in rows}
    home = (
        next((sg for sg in DEBT_DEFAULT_ORDER if sg in rows), None)
        if cls == "debt"
        else "gold_commodities"
    )
    return {sg: float(sg == home) for sg in rows}


def _weights_in_class(rec_by_sg: dict[str, float], inp) -> dict[str, float]:
    """`weight_in_class` for every settable row except the sleeve, which is one
    fund across all three classes and so no class's own row."""
    rows_by_class: dict[str, list[str]] = {}
    for sg in _settable_subcategory_ids():
        if sg != "multi_asset":
            rows_by_class.setdefault(CLASS_OF.get(sg, "others"), []).append(sg)

    weights: dict[str, float] = {}
    for cls, rows in rows_by_class.items():
        amounts = {sg: rec_by_sg.get(sg, 0.0) for sg in rows}
        if sum(amounts.values()) <= 0:
            amounts = _engine_default_split(cls, rows, inp)
        if sum(amounts.values()) <= 0:
            # Defensive — the engine placed nothing either. Still a whole class.
            amounts = dict.fromkeys(rows, 1.0)
        # Plain ratios: the screen normalises by their sum, so exact-sum
        # rounding here would buy nothing.
        total = sum(amounts.values())
        weights.update({sg: amt / total for sg, amt in amounts.items()})
    return weights


def subcategory_catalog(out, inp) -> list[ScreenSubcategory]:
    """Settable subcategories + Prozpr's recommended share of total, from a
    neutral practical-allocation run output and the input it was run on.

    Each row also carries `weight_in_class`, its share of its class's OWN rows,
    which the screen spreads a moved class bar by. It is the plan's own ratio,
    so an untouched class re-spreads to exactly the plan. A class the plan gave
    no money of its own — routinely debt and commodity, when the multi-asset
    fund carries all of both — has no ratio to read, and takes the split the
    engine would choose unprompted (`_engine_default_split`) instead.
    """
    grand = float(getattr(out, "grand_total", 0.0)) or 1.0
    rec_by_sg = {r.subgroup: float(r.total) for r in out.aggregated_subgroups}
    weights = _weights_in_class(rec_by_sg, inp)
    items: list[ScreenSubcategory] = []
    for sg in _settable_subcategory_ids():
        items.append(
            ScreenSubcategory(
                id=sg,
                label=subgroup_label(sg),
                recommended_pct_of_total=round(rec_by_sg.get(sg, 0.0) * 100.0 / grand, 1),
                weight_in_class=weights.get(sg),
                **{"class": CLASS_OF.get(sg, "others")},
            )
        )
    return items


def current_block(snapshot: HoldingsSnapshot) -> ScreenCurrent:
    """Where the customer sits today, in the screen's own rows (frontend spec
    2026-09-20 §3.1, D6).

    ``holdings`` carries every settable row as a share of the SETTABLE part of
    what they hold, so it sums to 100 (to the tenth — the frontend re-spreads
    the rounding). Everything else is ``excluded_pct``, a share of the WHOLE
    portfolio before that rescale: the frozen rows (ELSS, direct stock) the
    caption names, AND any held category this screen cannot set — dividend
    yield, silver, China, value whose metadata never classified (decision
    2026-09-26). Folding those in with the frozen rows keeps the figures
    honest: dropping them would inflate every settable row and report nothing
    excluded, so a customer 40% in a dividend fund would read as holding none
    of it.

    Nothing settable held → ``holdings`` is empty, which the screen reads as
    "nothing to show" (D8).
    """
    total = snapshot.total_inr
    settable = {sg: snapshot.by_subgroup.get(sg, 0.0) for sg in _settable_subcategory_ids()}
    settable_total = sum(settable.values())
    holdings = (
        [
            ScreenCurrentHolding(
                subgroup=sg, pct_of_total=round(amt * 100.0 / settable_total, 1)
            )
            for sg, amt in settable.items()
        ]
        if settable_total > 0
        else []
    )
    excluded = round((total - settable_total) * 100.0 / total, 1) if total > 0 else 0.0
    return ScreenCurrent(holdings=holdings, excluded_pct=excluded)


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

    # Frontend spec 2026-09-20 D1: today's holdings ride on this GET, off the
    # same snapshot the lump-sum deficit fill reads. The block is an adornment
    # the screen degrades gracefully without (D8), and the split it exists to
    # be compared against must still arrive — so a failed read costs the today
    # bar, not the screen. WARNING, so the failure reaches us, not just them.
    snapshot: Optional[HoldingsSnapshot]
    try:
        snapshot = await load_holdings_snapshot(db, user.id)
    except Exception:
        logger.warning(
            "investment-preferences: holdings snapshot failed; sending no today block",
            exc_info=True,
        )
        snapshot = None

    return ScreenPreferenceGetResponse(
        saved=saved,
        recommendation={"class_mix": class_rec},
        subcategories=subcategory_catalog(out, inp),
        multi_asset_composition=_MULTI_ASSET_COMPOSITION,
        # Spec 2026-09-15 §9.1. Read off the same profile the catalog was built
        # from, through the engine's own helper — so this warning and the §9
        # record attached after the run can never name different facts.
        carve_outs_at_risk=carve_outs_at_risk(inp),
        current=current_block(snapshot) if snapshot is not None else None,
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
