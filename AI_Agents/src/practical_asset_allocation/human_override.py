"""Customer preferences: the model they arrive in, and the outcome report.

Every facet is now honoured at the phase that DECIDES it (spec 2026-09-14):
the class split at phase 2 of the practical long-term step, the sub-group
pins and exclusions at phases 4 and 5. This module no longer reshapes
anything — it validates the ask and reports, off the finished allocation,
whether anything stopped it landing. Pure — no I/O, no DB, no LLM.

NOTE: the class facet moved upstream on 2026-09-14, so `asset_allocation_pydantic`
is no longer diff-free — `phase2_asset_class_pcts` takes an optional
`requested_class_pcts` that the practical orchestrator supplies.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, model_validator

from asset_allocation_pydantic.steps.step5_aggregation import CANONICAL_SUBGROUP_ORDER
from asset_allocation_pydantic.tables import SUBGROUP_TO_ASSET_CLASS

FROZEN_SUBGROUPS: frozenset[str] = frozenset(
    {"tax_efficient_equities", "non_mf_equities"}
)
SETTABLE_SUBGROUPS: frozenset[str] = frozenset(CANONICAL_SUBGROUP_ORDER)
ASSET_CLASSES = ("equity", "debt", "others")

# SUBGROUP_TO_ASSET_CLASS omits the two frozen practical-only rows; they ARE
# equity for class-total purposes (they never scale — see apply_human_override).
CLASS_OF: dict[str, str] = {
    **SUBGROUP_TO_ASSET_CLASS,
    "tax_efficient_equities": "equity",
    "non_mf_equities": "equity",
}

_SUM_TOLERANCE = 0.5  # percentage points
_MONEY_TOLERANCE = 500.0  # rupees — same conservation tolerance the tests use


def _check_sum_100(name: str, mix: dict[str, float]) -> None:
    total = sum(mix.values())
    if abs(total - 100.0) > _SUM_TOLERANCE:
        raise ValueError(f"{name} must sum to 100 (got {total})")


class HumanOverridePreferences(BaseModel):
    """What the customer asked for, resolved to absolutes app-side.

    ONE subgroup facet: ``subgroup_emphasis`` maps a subgroup to its share
    of the WHOLE portfolio (D-A3); an entry of 0 is a hard exclusion (the money
    must leave the row, crossing classes if it is the only row of its
    class). Market-cap asks arrive as emphasis on the beta subgroups
    (large→low_beta, mid→medium_beta, small→high_beta) — one vocabulary,
    matching the PAA output table. extra="forbid" so a stale caller passing
    a removed field fails loud instead of being ignored.
    """

    model_config = {"extra": "forbid"}

    asset_class_requested: Optional[dict[str, float]] = None
    subgroup_emphasis: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate(self) -> "HumanOverridePreferences":
        if self.asset_class_requested is not None:
            if set(self.asset_class_requested) != set(ASSET_CLASSES):
                raise ValueError(
                    "asset_class_requested must carry exactly equity/debt/others"
                )
            _check_sum_100("asset_class_requested", self.asset_class_requested)
            for cls, pct in self.asset_class_requested.items():
                if not 0.0 <= pct <= 100.0:
                    raise ValueError(
                        f"asset_class_requested[{cls}] must be between 0 and 100, "
                        f"got {pct}"
                    )
        for sg in self.subgroup_emphasis:
            if sg in FROZEN_SUBGROUPS:
                raise ValueError(f"{sg} is frozen and not settable")
            if sg not in SETTABLE_SUBGROUPS:
                raise ValueError(f"unknown subgroup {sg!r}")
        return self

    def is_empty(self) -> bool:
        return (
            self.asset_class_requested is None
            and not self.subgroup_emphasis
        )


BUCKET_COLS = ("emergency", "short_term", "medium_term", "long_term")


class HumanOverrideApplied(BaseModel):
    """The run's preference report (spec §6).

    Deliberately thin: what the customer ASKED for is the saved-preference
    row's own columns, and what they GOT is the run's asset-class breakdown —
    carrying either here just duplicated a number that already has an owner.
    What only this pass knows is whether anything stopped the ask landing.
    Its mere presence is the has-preference flag; ``preference_applied``
    states it for callers that hold the object rather than the output."""

    preference_applied: bool = True
    shortfall_reason: Optional[str] = None


# _scaled / _is_migratable / _apply_emphasis were DELETED (spec 2026-09-14).
# They were step 6's within-class reshape, retired in Part C — sub-group
# preferences are now honoured as inputs at phases 4 and 5. Nothing in
# production called them; the only caller left was their own unit test.


def excludes(prefs, subgroup: str) -> bool:
    """Did the customer refuse this sub-group outright?

    THE single definition of an exclusion: a share of zero or less is a hard
    refusal, not a pin of zero. Frozen rows (ELSS, direct stock) can never be
    refused — they are holdings the engine cannot trade. Both the engine's pin
    extraction and the customer-facing report read the rule from here, so the
    two can never drift apart.
    """
    if prefs is None or subgroup in FROZEN_SUBGROUPS:
        return False
    share = (prefs.subgroup_emphasis or {}).get(subgroup)
    return share is not None and share <= 0


# Spec 2026-09-15 §9. What each suspended carve-out cost, in the customer's
# words. Qualitative and amount-free on purpose: naming the ₹6,00,000 we would
# have set aside would mean running step 1 to learn it, and §3 is explicit that
# steps 1-3 genuinely do not run when a preference is set. Only the conditions
# that actually hold are named — telling a leveraged customer with no emergency
# need that we stopped carving their emergency fund would simply be false.
_SUSPENSION_PHRASES: dict[str, str] = {
    "emergency_fund": "setting aside a separate emergency fund",
    "near_term_goals": "earmarking money for goals in the next five years",
    "liability_offset": "holding back an amount against your borrowings",
}


def _join_phrases(parts: list[str]) -> str:
    """"a", "a or b", "a, b or c" — the notes are read as prose, not a list."""
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " or " + parts[-1]


def apply_human_override(
    output,
    prefs,
    multi_asset_composition=None,
    pins_scaled: bool = False,
    sleeve_clamped: bool = False,
    carve_outs_suspended: Optional[list[str]] = None,
):
    """THE reporting point. Always invoked; strict no-op when prefs is None
    or empty (golden-test guarantee). Pure — returns a new output.

    It no longer RESHAPES the allocation (spec 2026-09-14, Task C1). Every
    sub-group facet is honoured upstream now, so re-applying the same ask here
    applied it TWICE — against a migratable basis rather than the class the
    customer named — and this pass won: an 85/7/8 + low-beta-40% run placed
    ₹67,76,800 at phase 5 and left ₹47,47,520 in the row. What remains is
    validation (on the model) plus the customer-facing shortfall note, read
    off the FINISHED allocation.

    ``multi_asset_composition`` is retained for the call sites; the carved
    class mix the report needs is already on the output's own breakdown.
    ``pins_scaled`` is the engine's D-A4 flag: pins that over-drew their class
    room were scaled proportionally, and a trimmed pin is never silent.
    """
    if prefs is None or prefs.is_empty():
        return output, None

    # CARVED basis — multi_asset already split into its equity/debt/others
    # parts, the same basis the ask is expressed in (what the customer sees).
    block = output.asset_class_breakdown.recommended
    achieved = {
        "equity": block.equity_total_pct,
        "debt": block.debt_total_pct,
        "others": block.others_total_pct,
    }
    notes: list[str] = []
    if pins_scaled:
        # D-A4: the class split and locked holdings outrank a sub-group ask, so
        # over-subscribed pins scale down proportionally — say so.
        notes.append(
            "your category choices asked for more than their asset class "
            "holds — they were scaled proportionally to fit"
        )
    if sleeve_clamped:
        # D-A4 again, but a different shape: the multi-asset fund is ONE fund,
        # not a share of a class pool, so it is not scaled proportionally — it
        # is capped at the largest sleeve the class split can fund. Say that,
        # rather than borrowing the proportional wording above.
        notes.append(
            "your multi-asset choice was larger than your asset-class split "
            "can fund — it was reduced to the largest that fits"
        )
    # The class move lands at phase 2, so any gap here means something
    # genuinely constrained it — see the rule below.
    shortfall = None
    if prefs.asset_class_requested:
        req = prefs.asset_class_requested
        gaps = [
            f"{c}: asked {req[c]:.0f}%, landed {achieved[c]:.1f}%"
            for c in ASSET_CLASSES
            if abs(achieved[c] - req[c]) > 2.0
        ]
        if gaps:
            # Name the cause we KNOW before inferring one. Refusing gold zeroes
            # the commodity class (D-B3) and with it the multi-asset fund
            # (D-B2), which necessarily pushes the mix off the class target —
            # blaming "already committed" there is simply false for a customer
            # with no locked holdings and no buffer, which the heuristic below
            # cannot tell. Only when nothing known explains it do we infer:
            # at phase 2 the remaining things that can lift a class ABOVE its
            # ask are amounts committed before the preference applied — locked
            # ELSS / direct stock, or the emergency / goal buffers.
            if excludes(prefs, "gold_commodities"):
                lead = (
                    "excluding gold also removes the multi-asset fund that holds "
                    "it, so your commodity allocation moved to the other classes"
                )
            elif any(achieved[c] > req[c] + 0.5 for c in ASSET_CLASSES):
                lead = (
                    "what's already committed (locked ELSS / direct stock, or your "
                    "emergency & goal buffers) limits the move"
                )
            else:
                lead = "your category choices moved the mix off the class target"
            shortfall = lead + " — " + "; ".join(gaps)
    # The buffer is settled by step 1 and no preference facet reaches it any
    # more — the class split lands on the long-term remainder and the pins on
    # phases 4/5. So this is now a GUARD on the finished plan rather than an
    # expected outcome: if what the emergency bucket promises ever stops
    # matching what the rows carry, the customer hears about it.
    #
    # Spec 2026-09-15 §3 made `emergency_planned` always 0 under a preference,
    # so this guard can no longer fire for precisely the population it was
    # written for. It stays for the non-suspended paths; the suspension note
    # below is its replacement, and the two are mutually exclusive by
    # construction — nothing is planned when the carve-outs did not run.
    emergency_planned = sum(
        b.allocated_amount for b in output.bucket_allocations if b.bucket == "emergency"
    )
    emergency_rows = sum(r.emergency for r in output.aggregated_subgroups)
    if emergency_planned > 0 and emergency_rows < emergency_planned - _MONEY_TOLERANCE:
        cut_pct = (emergency_planned - emergency_rows) * 100.0 / emergency_planned
        notes.append(f"this reduces your emergency buffer by {cut_pct:.0f}%")
    suspended = [c for c in (carve_outs_suspended or []) if c in _SUSPENSION_PHRASES]
    if suspended:
        notes.append(
            "because you've set your own split, we're no longer "
            + _join_phrases([_SUSPENSION_PHRASES[c] for c in suspended])
        )
    if notes:
        shortfall = "; ".join(filter(None, [shortfall, *notes]))
    return output, HumanOverrideApplied(shortfall_reason=shortfall)
