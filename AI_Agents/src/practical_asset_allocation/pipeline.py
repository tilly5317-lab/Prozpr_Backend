"""practical_asset_allocation pipeline — see module __init__ docstring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from pydantic import BaseModel, Field

from asset_allocation_pydantic.models import (
    AggregatedRow,
    AggregatedSubgroupRow,
    AllocationInput,
    AssetClassAllocation,
    AssetClassBreakdown,
    AssetClassSplitBlock,
    BucketAllocation,
    BucketAssetClassSplit,
    ClientSummary,
    FutureInvestment,
    Goal,
    MultiAssetBlock,
    Step1Output,
    Step2Output,
    Step4Output,
    Step5Output,
    SubgroupBreakdown,
    SubgroupBucketAllocation,
    SubgroupBucketSplit,
)
from asset_allocation_pydantic.steps import (
    step1_emergency,
    step2_short_term,
    step5_aggregation,
)
from asset_allocation_pydantic.equity_subgroup_slider import (
    apply_equity_subgroup_slider,
)
from asset_allocation_pydantic.steps.step4_long_term import (
    ResolvedBounds,
    phase1_bounds,
    phase2_asset_class_pcts,
    phase4_multi_asset,
    phase5_equity_subgroups,
)
from asset_allocation_pydantic.tables import (
    EQUITY_SUBGROUPS,
    HORIZON_BOUNDARY_MONTHS,
    MULTI_ASSET_EQUITY_CAP_PCT,
    STEP4_SUBGROUPS,
    SUBGROUP_TO_ASSET_CLASS,
)
from asset_allocation_pydantic.utils import round_to_100
from practical_asset_allocation.allocation_snap import apply_current_allocation_snap
from practical_asset_allocation.human_override import (
    ASSET_CLASSES,
    excludes,
    CLASS_OF,
    FROZEN_SUBGROUPS,
    HumanOverrideApplied,
    HumanOverridePreferences,
    apply_human_override,
)


# The settable long-term debt rows, derived so a new one in the table cannot be
# missed here. `arbitrage` joined them on 2026-09-15 (§4).
DEBT_SUBGROUPS: tuple[str, ...] = tuple(
    sg for sg in STEP4_SUBGROUPS if SUBGROUP_TO_ASSET_CLASS[sg] == "debt"
)
# Where the residual goes when the customer named NO debt row. Order matters —
# the second entry is the D-B5 reroute when the first is excluded. `arbitrage`
# is deliberately not here: the engine must never pick it unasked (§4).
DEBT_DEFAULT_ORDER: tuple[str, ...] = ("arbitrage_plus_income", "short_debt")

# How large a sleeve trim has to be before the customer is TOLD about it, as a
# share of the portfolio (spec 2026-09-15 §8.1, amended 2026-09-15 after
# measurement). The preferences screen states every row to one decimal place,
# so a complete distribution can only ever be self-consistent to that
# precision: a funded row rounding UP eats the sleeve's room, and with the
# sleeve drawing equity at 0.65 the slop is divided by that again. Three funded
# equity rows are worth 3 x 0.05 / 0.65 = 0.23pp of sleeve, and a customer who
# accepts our own recommendation verbatim lands squarely inside it (measured:
# 0.161pp). Disclosing that reads as "your multi-asset choice was too large"
# for a number WE gave them. Anything past the floor is a genuine shortfall and
# is still disclosed — the near-term-goal case trims 0.924pp and says so.
SLEEVE_CLAMP_DISCLOSURE_FLOOR_PCT: float = 0.25

# Spec §B.5 step 4 — practical-side others-gate (stricter than upstream).
# Upstream uses score >= 8 AND view <= 6; practical uses score > 8 AND view < 7.
PRACTICAL_OTHERS_GATE_SCORE_THRESHOLD: float = 8.0
PRACTICAL_OTHERS_GATE_VIEW_THRESHOLD: float = 7.0

# Spec §B.5 step 7 (R182) — NFA-banded max non-MF equity %.
# > 5Cr → 75%, > 2Cr → 60%, > 1Cr → 50%, else → 33%.
NFA_BAND_5CR_INR: float = 50_000_000.0
NFA_BAND_2CR_INR: float = 20_000_000.0
NFA_BAND_1CR_INR: float = 10_000_000.0
NFA_BAND_PCT_ABOVE_5CR: float = 0.75
NFA_BAND_PCT_ABOVE_2CR: float = 0.60
NFA_BAND_PCT_ABOVE_1CR: float = 0.50
NFA_BAND_PCT_DEFAULT: float = 0.33


def _nfa_banded_max_non_mf_equity_pct(nfa: Optional[float]) -> float:
    """R182: returns the NFA-banded max non-MF equity %. Treats None NFA as the
    bottom band (33%) — defensive: callers normally pass NFA always."""
    if nfa is None:
        return NFA_BAND_PCT_DEFAULT
    if nfa > NFA_BAND_5CR_INR:
        return NFA_BAND_PCT_ABOVE_5CR
    if nfa > NFA_BAND_2CR_INR:
        return NFA_BAND_PCT_ABOVE_2CR
    if nfa > NFA_BAND_1CR_INR:
        return NFA_BAND_PCT_ABOVE_1CR
    return NFA_BAND_PCT_DEFAULT


class InfeasibleGoalError(ValueError):
    """Raised when the input corpus cannot satisfy structural constraints
    (e.g. ELSS holdings exceed total corpus)."""


class PracticalAllocationInput(AllocationInput):
    """Extends AllocationInput with four holdings-aware corpus scalars.

    Implicit corpus accounting (not separate inputs):
      cash               = total_corpus - mf_corpus - non_mf_equity_corpus
      mf_non_elss        = mf_corpus - elss_corpus
      rebalancing_corpus = total_corpus - elss_corpus
    """

    mf_corpus: float = Field(..., ge=0)
    """Total MF holdings INCLUDING ELSS."""

    non_mf_equity_corpus: float = Field(default=0.0, ge=0)
    """Direct stocks + PMS — non-MF equity, treated separately because the
    rebalancing engine can't trade them per-fund."""

    elss_corpus: float = Field(default=0.0, ge=0)
    """ELSS MF holdings (subset of mf_corpus). Locked under 3-year SEBI
    lock-in — surfaced as a frozen long-term row."""

    max_non_mf_equity_pct_client_input: Optional[float] = Field(default=None)
    """Advisor override for the NFA-banded non-MF equity cap (Option A)."""

    human_override: Optional[HumanOverridePreferences] = None
    """Standing/one-off customer preference. None → the human_override step is
    a strict no-op (golden-test guarantee). Loaded ONLY by the app-side PAA
    input builder — engines never read the DB."""

    current_subgroup_allocation: Optional[dict[str, float]] = None
    """Customer's present MF holdings per `asset_subgroup`, in rupees. Feeds the
    snap-to-current step (spec 2026-09-21). None → the snap is a strict no-op,
    which keeps the golden/contract suite byte-identical. Populated by every real
    caller (app input builder, sim, sweep) from the customer's holdings."""


class CorpusBreakdown(BaseModel):
    """Practical-only block: how the customer's corpus splits across MF /
    non-MF equity / cash, and what the engine actually deployed.

    All amounts are rupees rounded to whole integers; the engine internally
    works in floats and rounds at the boundary.
    """

    total_corpus_inr: int = Field(..., ge=0)
    mf_corpus_inr: int = Field(..., ge=0)
    non_mf_equity_input_inr: int = Field(..., ge=0)
    """Echo of the input — what the customer said they hold."""
    elss_corpus_inr: int = Field(..., ge=0)
    rebalancing_corpus_inr: int = Field(..., ge=0)
    """total_corpus_inr - elss_corpus_inr (ELSS is frozen)."""
    non_mf_equity_actual_inr: int = Field(..., ge=0)
    """<= input, NFA-capped — what the engine could absorb."""
    excess_direct_stocks_inr: int = Field(..., ge=0)
    """input - actual; drives the SELL_DIRECT_STOCKS recommendation downstream."""
    max_non_mf_equity_pct_computed: float = Field(..., ge=0.0, le=1.0)
    """NFA-banded value used (or override if the advisor provided one)."""
    lt_equities_amount_inr: int = Field(..., ge=0)
    """Long-term equity budget (Excel R177). Denominator for the non-MF cap."""
    non_mf_equity_cap_inr: int = Field(..., ge=0)
    """Absolute INR cap on non-MF equity = max_pct × lt_equities_amount."""


class PracticalAllocationOutput(BaseModel):
    """Shape-parity with GoalAllocationOutput (same seven fields) plus one
    extras block (corpus_breakdown).

    Any consumer that already understands GoalAllocationOutput handles
    PracticalAllocationOutput for the shared seven fields with zero change.
    """

    client_summary: ClientSummary
    bucket_allocations: List[BucketAllocation]
    aggregated_subgroups: List[AggregatedSubgroupRow]
    """Same shape as GoalAllocationOutput.aggregated_subgroups, but includes
    two extra rows: 'tax_efficient_equities' (ELSS amount in long_term column)
    and 'non_mf_equities' (non-MF equity actual in long_term column)."""
    future_investments_summary: List[FutureInvestment]
    grand_total: float
    all_amounts_in_multiples_of_100: bool
    asset_class_breakdown: AssetClassBreakdown
    corpus_breakdown: CorpusBreakdown
    human_override_applied: Optional[HumanOverrideApplied] = None


@dataclass
class _PracticalLongTermResult:
    """Internal carrier for the long-term step output. Filled in across
    Tasks 5-10; output assembly (Tasks 11-12) reads from here."""

    # R157-R165 (Task 5):
    total_long_term_corpus: int
    min_equity_elss_pct: float
    phase1_bounds_allocation_1: ResolvedBounds
    # R167-R174 (Task 6):
    practical_others_gate_fired: bool
    allocation_2_equity_pct: int
    allocation_2_debt_pct: int
    allocation_2_others_pct: int
    # R177-R186 (Task 7):
    equities_amount: int
    debt_amount: int
    others_amount: int
    elss_amount_frozen: int
    max_non_mf_equity_pct_computed: float
    max_non_mf_equity_pct_considered: float
    max_equities_shares: int
    non_mf_equity_actual: int
    excess_direct_stocks: int
    residual_equity_corpus_pre_multi_asset: int
    # R187-R194 (Task 8):
    multi_asset_block: MultiAssetBlock
    multi_asset_others_excess: int
    excess_to_debt: int
    excess_to_equity: int
    residual_equity_corpus_final: int
    residual_debt_corpus: int
    # R196-R215 (Task 9):
    average_equity_subgroup_allocation_pct: float
    min_equity_pct_required: float
    equity_subgroup_amounts: dict[str, int]  # one entry per EQUITY_SUBGROUPS
    # Pre-slider state — what the slider actually compared against
    # min_equity_pct_required when deciding which subgroups to drop. Final
    # amounts (above) are post-drop-and-redistribute, so they no longer match
    # the threshold comparison. Surfaced for Excel-row debugging.
    initial_equity_subgroup_amounts: dict[str, int]
    # Denominator for the slider's per-subgroup share % (Excel R194+R187+R181).
    equity_share_denominator: int
    # R217-R222 (Task 10):
    residual_other_corpus: int
    long_term_subgroup_amounts: dict[str, int]  # one entry per STEP4_SUBGROUPS
    goals_allocated: List[Goal]
    future_investment: Optional[FutureInvestment]
    # D-A4 disclosure: the customer's pins over-drew a class room and had to
    # scale down proportionally. Carried out of the engine so the output step
    # can say so (Task C1) — a pin is never silently trimmed.
    pins_scaled: bool = False
    sleeve_clamped: bool = False


def carve_outs_at_risk(inp: AllocationInput) -> list[str]:
    """Which bucket carve-outs a stated preference would cost this customer —
    spec 2026-09-15 §9/§9.1. Read off the PROFILE, so it answers both moments:
    the warning the screen shows BEFORE the customer commits, and the record
    attached to the run afterwards. One source of truth, or the two disagree.

    Three values rather than one flag because they are three different facts
    with three different triggers. The NFA offset in particular is a LIABILITY
    offset computed regardless of `emergency_fund_needed` (§3.4), so a leveraged
    customer with no emergency-fund need must be told about the offset and
    nothing else — which a single boolean would have got wrong.

    NOTE: `emergency_fund_needed` is hardcoded `False` by the app-side input
    builder (not yet a DB column), so today only the other two can fire on the
    screen's path. The condition is written out anyway — it is the profile's
    own flag, and it starts working the day the column lands.
    """
    at_risk: list[str] = []
    if inp.emergency_fund_needed:
        at_risk.append("emergency_fund")
    if any(
        g.time_to_goal_months < HORIZON_BOUNDARY_MONTHS + inp.months_to_fy_end
        for g in inp.goals
    ):
        # Not merely a lost bucket linkage: step 4 only selects goals at or past
        # the boundary, so a nearer one leaves the plan entirely (§3.3).
        at_risk.append("near_term_goals")
    nfa = inp.net_financial_assets
    if nfa is not None and nfa < 0:
        at_risk.append("liability_offset")
    return at_risk


def _split_pro_rata(total: int, asks: dict[str, int]) -> dict[str, int]:
    """Split ``total`` across ``asks`` in proportion to them — spec 2026-09-15 §7.

    Rounded to ₹100 like every other amount the engine emits, with the whole
    remainder parked on the largest ask so the rupees always conserve exactly.
    Ties break on the name so the result is deterministic.
    """
    ask_total = sum(asks.values())
    if ask_total <= 0:
        return {sg: 0 for sg in asks}
    out = {sg: round_to_100(total * amt / ask_total) for sg, amt in asks.items()}
    largest = max(asks, key=lambda sg: (asks[sg], sg))
    out[largest] = max(0, out[largest] + total - sum(out.values()))
    return out


def _no_carveout_buckets(
    rebalancing_corpus: float,
) -> tuple[Step1Output, Step2Output]:
    """Zeroed emergency / short outputs carrying the whole corpus forward —
    spec 2026-09-15 §3.

    `asset_subgroup` (step 2) is a required `Literal` field with no default, so a
    zeroed instance has to name one. The value below is INERT: nothing is
    allocated, so no row can receive it. It surfaces only in the `trace` dict,
    which no production caller passes.
    """
    # int(), not round(): step 1 derives its own remaining_corpus with
    # `int(inp.total_corpus)`, and a fractional ELSS corpus makes the two differ
    # by a rupee. The suspended path must hand the long-term step the same
    # number the carved path would have.
    corpus = int(rebalancing_corpus)
    return (
        Step1Output(
            emergency_fund_months=0,
            emergency_fund_amount=0,
            nfa_carveout_amount=0,
            total_emergency=0,
            remaining_corpus=corpus,
            subgroup_amounts={},
        ),
        Step2Output(
            goals_allocated=[],
            asset_subgroup="short_debt",
            total_goal_amount=0,
            allocated_amount=0,
            remaining_corpus=corpus,
            subgroup_amounts={},
        ),
    )


def _committed_by_class(*steps) -> dict[str, float]:
    """Rupees already allocated per asset class by the emergency / short
    steps (their ``subgroup_amounts``), rolled up via the subgroup→class
    table. Direct indexing is deliberate: an unmapped subgroup must fail loud,
    not be silently counted as commodity and skew the split. NOTE: the table
    maps ``multi_asset`` flatly to equity; safe here because steps 1-3 only
    ever emit debt subgroups, but do NOT reuse this for a bucket that could
    hold multi_asset without splitting it by the fund composition first."""
    out = {c: 0.0 for c in ASSET_CLASSES}
    for s in steps:
        for sg, amt in s.subgroup_amounts.items():
            out[SUBGROUP_TO_ASSET_CLASS[sg]] += float(amt)
    return out


def _lt_class_targets_from_overall(
    requested: dict[str, float],
    total_corpus: float,
    committed: dict[str, float],
) -> dict[str, float]:
    """Turn an OVERALL-portfolio class preference (% of total) into the
    long-term step's class split (% of the LT corpus) — spec 2026-09-14 §4.1.

    ``LT_target[c] = max(0, total × pref[c]/100 − committed[c])``, then the three
    are scaled proportionally to fill the LT corpus exactly (D4). A class a
    bucket already over-holds gets 0 here, so the overall lands at the
    committed amount — what's already committed wins; step 6 discloses it
    from the final numbers. With nothing committed this is the identity.
    """
    raw = {
        c: max(0.0, total_corpus * requested[c] / 100.0 - committed.get(c, 0.0))
        for c in ASSET_CLASSES
    }
    total = sum(raw.values())
    if total <= 0:
        # Degenerate: the buckets already hold everything the customer asked
        # for. Fall back to the requested split rather than zeros, which phase
        # 2's largest-absorbs-drift would turn into a surprising 100% equity.
        return dict(requested)
    return {c: raw[c] * 100.0 / total for c in ASSET_CLASSES}


def _subgroup_pins(
    prefs: Optional[HumanOverridePreferences],
    total_corpus: float,
) -> tuple[dict[str, int], frozenset[str], bool]:
    """Turn the customer's sub-group asks into rupee pins — spec 2026-09-14 §5.

    Returns ``(pins, excluded, gold_excluded)``. A share ``<= 0`` is a hard
    exclusion, not a pin of zero: the row must be emptied, and phase 5 has to
    drop it BEFORE the tilt or it gets quietly refilled.

    ``gold_excluded`` is D-B3 surfaced to the caller. ``gold_commodities`` is
    the only dedicated commodity vehicle, so excluding it leaves the commodity
    class with no home — the caller must zero the commodity class before
    sizing the sleeve (which by D-B2 then yields no sleeve at all) and
    disclose it, since the multi-asset fund was delivering the debt
    tax-efficiently. Debt needs no equivalent: excluding the debt bucket just
    forces the sleeve to carry all the debt, which is feasible.

    The frozen rows (ELSS, direct stock) are ignored — they are HOLDINGS the
    engine cannot trade, not preferences. ``HumanOverridePreferences`` already
    rejects them; skipping them here keeps the helper honest for any other
    caller.

    ``subgroup_emphasis`` is a share of the WHOLE portfolio (D-A3), so a pin
    is one multiply against ``total_corpus`` and needs no class amounts —
    which is what lets the caller size the long-term step in a single run.
    """
    pins: dict[str, int] = {}
    excluded: set[str] = set()
    emphasis = prefs.subgroup_emphasis if prefs is not None else {}
    for sg, share in emphasis.items():
        if sg in FROZEN_SUBGROUPS:
            continue
        if excludes(prefs, sg):
            excluded.add(sg)
            continue
        pins[sg] = round_to_100(total_corpus * share / 100.0)
    return pins, frozenset(excluded), "gold_commodities" in excluded


def _fit_pins_to_room(
    pins: dict[str, int],
    room: int,
) -> tuple[dict[str, int], bool]:
    """Reconcile pins to the pool that has to hold them — D-A4.

    Over-subscribed pins scale down PROPORTIONALLY and the bool comes back
    True so the caller discloses it; a pin is never silently dropped. Returns
    the pins untouched (and False) whenever they already fit.
    """
    total = sum(pins.values())
    if not pins or total <= room:
        return dict(pins), False
    if room <= 0:
        return {sg: 0 for sg in pins}, True
    scaled = {sg: round_to_100(amt * room / total) for sg, amt in pins.items()}
    # round_to_100 rounds half UP, so the scaled set can overshoot `room` by up
    # to ₹50 a pin. Phase 5 raises when the pins exceed its pool, so shave the
    # drift off the largest rather than hand the caller an unfittable set.
    drift = sum(scaled.values()) - room
    if drift > 0:
        largest = max(scaled, key=lambda k: scaled[k])
        scaled[largest] = max(0, scaled[largest] - drift)
    return scaled, True


def _sleeve_size(
    deployable_equity: float,
    debt: float,
    others: float,
    pins: dict[str, int],
    eq_pct: float,
    dt_pct: float,
    oth_pct: float,
    requested: Optional[int] = None,
    preference_set: bool = False,
) -> int:
    """How large the multi-asset sleeve may be — the uniform min() (spec §5).

    Gold and the long-term debt bucket are pure residuals of their class
    (``others − 0.10×sleeve`` and ``debt − 0.25×sleeve``), so a pin on either
    has the same shape as a pin on equity: a ceiling on the sleeve. Every
    sub-group preference therefore collapses into one rule, and because each
    residual is what the sleeve leaves behind, the min() guarantees no pin is
    ever UNDER-satisfied — exactly one room binds and the rest come out at or
    above their ask (D-A5). Never "correct" a residual back toward its pin;
    that would strand rupees and break conservation.

    ``eq_pct``/``dt_pct``/``oth_pct`` are the fund composition as FRACTIONS
    (0.65 / 0.25 / 0.10), matching phase4_multi_asset's internals — plain
    floats, so this stays pure arithmetic.

    ``deployable_equity`` is the post-ELSS, post-direct-stock equity residual:
    what can actually be bought. ``preference_set`` means ANY preference,
    class or sub-group (D-B4). The caller must already have applied D-B3 —
    with gold excluded, ``others`` arrives as 0 and the sleeve falls out at 0.
    A sleeve the customer REFUSED arrives as ``requested=0`` and falls out the
    same way; an absent ask is ``None``, which is not the same thing.

    With ``requested=None``, no pins and ``preference_set=False`` this is
    today's auto-size exactly; that equivalence is pinned by
    TestSleeveSizeNoDrift and must never be relaxed.
    """
    INF = float("inf")

    # Bucket the pins by the class that funds them. multi_asset is skipped on
    # purpose: CLASS_OF maps it flatly to equity, but the sleeve IS the
    # multi-asset ask and arrives as `requested` — counting it here too would
    # charge it against the equity room twice.
    equity_pins = sum(
        amt
        for sg, amt in pins.items()
        if sg != "multi_asset" and CLASS_OF[sg] == "equity"
    )
    debt_pins = sum(amt for sg, amt in pins.items() if CLASS_OF[sg] == "debt")
    others_pins = sum(amt for sg, amt in pins.items() if CLASS_OF[sg] == "others")

    rooms = [
        # EQUITY class — the sleeve's equity slice and the customer's equity
        # pins are both funded out of the deployable equity residual.
        (deployable_equity - equity_pins) / eq_pct if eq_pct > 0 else INF,
        # DEBT class — what is left once the debt pin is set aside. Feasibility:
        # a slice that over-draws its class is simply unaffordable.
        (debt - debt_pins) / dt_pct if dt_pct > 0 else INF,
    ]
    if preference_set:
        # COMMODITY class — bounded ONLY with a preference (D-B1). On the
        # default path the others-gate can zero commodity, and bounding by
        # others/0.10 = 0 would destroy a sleeve that is fine today. With a
        # preference the class split is the outer truth, so the bound applies
        # and the commodity target lands exactly.
        rooms.append((others - others_pins) / oth_pct if oth_pct > 0 else INF)

    if requested is None:
        # DIVERSIFICATION cap — POLICY, not feasibility (D-A6): the engine's own
        # "anchor without dominating" judgement, and the auto path's alone. A
        # customer who names a sleeve size has made that judgement themselves,
        # so a request drops this term and keeps only the three class rooms.
        rooms.append(
            (MULTI_ASSET_EQUITY_CAP_PCT * deployable_equity) / eq_pct
            if eq_pct > 0
            else INF
        )
    else:
        rooms.append(float(requested))

    candidate = min(rooms)
    # Same guards as phase4_multi_asset: no equity or no debt to draw on means
    # no sleeve, and an all-INF candidate means the fund holds neither.
    if candidate == INF or candidate <= 0 or deployable_equity <= 0 or debt <= 0:
        return 0
    return round_to_100(candidate)


def _run_practical_long_term(
    *,
    inp: AllocationInput,
    remaining_corpus: int,
    elss_amount: float,
    non_mf_equity_input: float,
    nfa: Optional[float],
    max_non_mf_equity_pct_client_input: Optional[float],
    requested_class_pcts: Optional[dict[str, float]] = None,
    subgroup_pins: Optional[dict[str, int]] = None,
    subgroup_excluded: frozenset[str] = frozenset(),
    preference_set: bool = False,
) -> _PracticalLongTermResult:
    """Long-term step — Excel R157-R222. Holdings-aware.

    Layout (split across Tasks 5-10):
      Task 5  (R157-R165): corpus assembly, ELSS floor, first-level bounds.
      Task 6  (R167-R174): others-gate, second-level allocation pct.
      Task 7  (R177-R186): amounts, ELSS, non-MF cap, residual_equity.
      Task 8  (R187-R194): multi-asset block.
      Task 9  (R196-R215): equity subgroup gates, slider, amounts.
      Task 10 (R217-R222): debt and others residuals.

    ``subgroup_pins`` are the customer's sub-group asks in RUPEES and
    ``subgroup_excluded`` the rows they emptied (spec 2026-09-14 §5). Every pin
    — equity category, gold, the debt bucket or the sleeve itself — is one
    thing: a ceiling on how large the multi-asset sleeve may be, because gold
    and the long-term debt bucket are pure residuals of their class. Only the
    equity pins are PLACED (phase 5); the gold and debt asks stay residuals and
    D-A5 guarantees the residual lands at or above the ask.

    ``preference_set`` means ANY preference, class or sub-group (D-B4). It is
    the switch between the default path — which must stay byte-identical — and
    the preference path, so it gates the sleeve request, never the pins alone.
    """
    # R-pre: filter long-term goals using HORIZON_BOUNDARY_MONTHS (same
    # operator as upstream step4_long_term.run). Emit FutureInvestment when
    # corpus is short of the goal sum (spec §B.7 edge case β).
    lt_goals = [
        g for g in inp.goals
        if g.time_to_goal_months >= HORIZON_BOUNDARY_MONTHS + inp.months_to_fy_end
    ]
    sum_goals = round_to_100(sum(g.amount_needed for g in lt_goals))
    future_investment: Optional[FutureInvestment] = None
    if sum_goals > remaining_corpus:
        future_investment = FutureInvestment(
            bucket="long_term",
            future_investment_amount=sum_goals - remaining_corpus,
        )

    # R158: long-term corpus includes ELSS added back (ELSS is locked but
    # counted toward the long-term equity-class budget).
    total_long_term_corpus = max(0, int(remaining_corpus + elss_amount))

    # R159: ELSS-as-floor share of long-term equity.
    if total_long_term_corpus > 0:
        min_equity_elss_pct = elss_amount / total_long_term_corpus
    else:
        min_equity_elss_pct = 0.0

    # R161-R165: first-level asset-class bounds from PHASE1_RISK_BOUNDS,
    # reused verbatim from asset_allocation_pydantic.
    bounds_1 = phase1_bounds(
        score=inp.effective_risk_score,
        market_commentary=inp.market_commentary,
        goals=[],  # phase1_bounds does not use goals; pass empty for now.
        intergenerational_transfer=inp.intergenerational_transfer,
    )

    # R167-R168: stricter practical others-gate. Note: phase1_bounds already
    # applied the upstream gate (score >= 8 AND view <= 6) inside bounds_1.
    # We layer the stricter variant (score > 8 AND view < 7) on top so the
    # practical engine zeros others slightly earlier than the ideal engine.
    practical_others_gate_fired = (
        inp.effective_risk_score > PRACTICAL_OTHERS_GATE_SCORE_THRESHOLD
        and inp.market_commentary.others < PRACTICAL_OTHERS_GATE_VIEW_THRESHOLD
    )
    bounds_for_phase2 = bounds_1
    if practical_others_gate_fired and (
        bounds_1.others_min > 0 or bounds_1.others_max > 0
    ):
        # Pro-rata redistribute the zeroed others to equity and debt mins.
        freed_max = bounds_1.others_max
        freed_min = bounds_1.others_min
        eq_max_new = bounds_1.eq_max
        debt_max_new = bounds_1.debt_max
        eq_min_new = bounds_1.eq_min
        debt_min_new = bounds_1.debt_min
        total_max = bounds_1.eq_max + bounds_1.debt_max
        if total_max > 0 and freed_max > 0:
            eq_add = int(round(freed_max * bounds_1.eq_max / total_max))
            eq_max_new += eq_add
            debt_max_new += freed_max - eq_add
        total_min = bounds_1.eq_min + bounds_1.debt_min
        if total_min > 0 and freed_min > 0:
            eq_add_min = int(round(freed_min * bounds_1.eq_min / total_min))
            eq_min_new += eq_add_min
            debt_min_new += freed_min - eq_add_min
        bounds_for_phase2 = ResolvedBounds(
            eq_min=eq_min_new,
            eq_max=eq_max_new,
            debt_min=debt_min_new,
            debt_max=debt_max_new,
            others_min=0,
            others_max=0,
        )

    # R170: market-view tilt → phase2_asset_class_pcts (reused upstream).
    # With a customer class preference the requested split IS the output —
    # tilt and both others-gates bypassed (spec 2026-09-14 D3). Everything
    # below (ELSS floor, pro-rata redistribution, amounts) runs unchanged, and
    # because the redistribution uses these same raws, a floored equity keeps
    # the customer's own debt:others ratio (D4) for free.
    a2_eq_pct_raw, a2_debt_pct_raw, a2_oth_pct_raw = phase2_asset_class_pcts(
        bounds_for_phase2,
        inp.market_commentary,
        requested_class_pcts=requested_class_pcts,
    )

    # R171: ELSS floor lifts equity allocation if needed.
    elss_floor_pct_int = int(round(min_equity_elss_pct * 100))
    allocation_2_equity_pct = max(a2_eq_pct_raw, elss_floor_pct_int)

    # R172: pro-rata redistribution of the residual into debt / others.
    # Excel formula: (100-F171) * E172 / (E172+E173) where E172/E173 are the
    # allocation_1 tilted averages. We use the same denominator (the upstream
    # phase2 tilted raws), not the phase1 mins, so an asset class with mins=0
    # still receives its tilt-driven share.
    remaining_pct = 100 - allocation_2_equity_pct
    if remaining_pct <= 0:
        allocation_2_debt_pct = 0
        allocation_2_others_pct = 0
        # Force-clamp equity at 100 if the ELSS floor overshot.
        allocation_2_equity_pct = 100
    else:
        dt_oth_raw = a2_debt_pct_raw + a2_oth_pct_raw
        if dt_oth_raw > 0:
            allocation_2_debt_pct = int(
                round(remaining_pct * a2_debt_pct_raw / dt_oth_raw)
            )
            allocation_2_others_pct = remaining_pct - allocation_2_debt_pct
        else:
            # Degenerate: both raws zero. All residual → debt by default.
            allocation_2_debt_pct = remaining_pct
            allocation_2_others_pct = 0

    # R177-R179: amounts.
    equities_amount = round_to_100(
        total_long_term_corpus * allocation_2_equity_pct / 100
    )
    others_amount = round_to_100(total_long_term_corpus * allocation_2_others_pct / 100)
    debt_amount = max(0, total_long_term_corpus - equities_amount - others_amount)
    debt_amount = round_to_100(debt_amount)

    # Reconcile rounding drift onto the largest amount (mirrors upstream pattern).
    drift = total_long_term_corpus - (equities_amount + debt_amount + others_amount)
    if drift != 0:
        amounts_by_name = {
            "eq": equities_amount,
            "dt": debt_amount,
            "oth": others_amount,
        }
        largest = max(amounts_by_name, key=lambda k: amounts_by_name[k])
        amounts_by_name[largest] += drift
        equities_amount = max(0, amounts_by_name["eq"])
        debt_amount = max(0, amounts_by_name["dt"])
        others_amount = max(0, amounts_by_name["oth"])

    # D-B3: gold_commodities is the only DEDICATED commodity vehicle, so
    # excluding it leaves the commodity class with no home — and parking the
    # class in the sleeve instead is infeasible (an 8% commodity ask would need
    # a sleeve worth 80% of the corpus, whose 25% debt slice breaks any sane
    # debt target). The class therefore goes to ZERO here, which by D-B2 also
    # zeroes the sleeve: the sleeve is 10% commodity by construction, so "no
    # gold" really does mean "no multi-asset fund". The freed rupees go to
    # EQUITY, not debt — the debt target is the customer's own number and must
    # still land, delivered by dedicated debt funds once the sleeve is gone.
    if "gold_commodities" in subgroup_excluded and others_amount > 0:
        equities_amount += others_amount
        others_amount = 0
        allocation_2_equity_pct += allocation_2_others_pct
        allocation_2_others_pct = 0

    # R180: ELSS frozen amount.
    elss_amount_frozen = int(round(elss_amount))

    # R182-R184: NFA-banded cap + advisor override (Option A — client wins).
    max_non_mf_equity_pct_computed = _nfa_banded_max_non_mf_equity_pct(nfa)
    max_non_mf_equity_pct_considered = (
        max_non_mf_equity_pct_client_input
        if max_non_mf_equity_pct_client_input is not None
        else max_non_mf_equity_pct_computed
    )

    # R185: ceiling for non-MF equity absorption.
    max_equities_shares = int(round(max_non_mf_equity_pct_considered * equities_amount))

    # R186: non-MF actual = min(input, equities_amount - elss, max_equities_shares).
    available_after_elss = max(0, equities_amount - elss_amount_frozen)
    non_mf_equity_actual = int(
        round(
            min(
                non_mf_equity_input,
                available_after_elss,
                max_equities_shares,
            )
        )
    )
    non_mf_equity_actual = max(0, non_mf_equity_actual)

    # Excess (drives SELL_DIRECT_STOCKS downstream in Rebalancing).
    excess_direct_stocks = max(
        0,
        int(round(non_mf_equity_input)) - non_mf_equity_actual,
    )

    # Residual equity corpus available for MF subgroups (pre-multi-asset).
    residual_equity_corpus_pre_multi_asset = max(
        0,
        equities_amount - non_mf_equity_actual - elss_amount_frozen,
    )

    # Reconcile the pins to the class rooms BEFORE sizing the sleeve: the class
    # split is the outer truth and locked holdings outrank an ask (D-A4).
    # Over-subscribed pins scale down proportionally and set the disclosure
    # flag — never silently dropped.
    pins = dict(subgroup_pins or {})
    pins_scaled = False
    sleeve_clamped = False
    if pins:
        pin_rooms = {
            # Equity pins are funded out of what can actually be BOUGHT: the
            # locked ELSS and direct-stock rupees are already carved off here.
            "equity": residual_equity_corpus_pre_multi_asset,
            "debt": debt_amount,
            "others": others_amount,
        }
        fitted: dict[str, int] = {}
        for cls, room in pin_rooms.items():
            # multi_asset is held out: CLASS_OF files it under equity, but it
            # is the sleeve ASK rather than a claim on the equity pool, and
            # _sleeve_size clamps it against all three class rooms itself.
            group = {
                sg: amt
                for sg, amt in pins.items()
                if sg != "multi_asset" and CLASS_OF[sg] == cls
            }
            if not group:
                continue
            group, scaled = _fit_pins_to_room(group, room)
            pins_scaled = pins_scaled or scaled
            fitted.update(group)
        if "multi_asset" in pins:
            fitted["multi_asset"] = pins["multi_asset"]
        pins = fitted

    # R187: multi-asset block. The upstream helper already caps the multi-asset
    # equity slice at MULTI_ASSET_EQUITY_CAP_PCT and rounds to 100. We feed it
    # the practical RESIDUAL equity (post-ELSS, post-non-MF) rather than
    # equities_amount, so the multi-asset cap respects what we can actually
    # deploy via MFs.
    comp = inp.multi_asset_composition
    if preference_set:
        # THE UNIFORM CARVE (spec §5): one min() fixes the sleeve and every
        # residual falls out of it. `requested_amount` is passed ONLY on this
        # branch. phase 4's REQUESTED path clamps by the commodity room while
        # its AUTO path does not, and on the default path the others-gate can
        # legitimately zero commodity — the risk-9.5 profile runs a ₹1.1cr
        # sleeve with others == 0 — so handing the default path a request would
        # collapse that sleeve to nothing. Hence the branch, not a default arg.
        #
        # A REFUSED sleeve is a sleeve of zero, not an absent ask (D-A2): an
        # exclusion leaves no `multi_asset` key among the pins, so without this
        # the request would be None and the sleeve would auto-size straight
        # back to the fund the customer declined. Its slices simply return to
        # the dedicated rows of the classes that were funding them.
        requested_sleeve = (
            0 if "multi_asset" in subgroup_excluded else pins.get("multi_asset")
        )
        sized_sleeve = _sleeve_size(
            deployable_equity=residual_equity_corpus_pre_multi_asset,
            debt=debt_amount,
            others=others_amount,
            pins=pins,
            eq_pct=comp.equity_pct / 100.0,
            dt_pct=comp.debt_pct / 100.0,
            oth_pct=comp.others_pct / 100.0,
            requested=requested_sleeve,
            preference_set=True,
        )
        # D-A4: a pin that had to be trimmed is never silent. A multi-asset ask
        # is held out of _fit_pins_to_room (it is one fund, not a share of a
        # class pool), so its own trim has to be noticed here — the class rooms
        # in _sleeve_size can clamp it below what the customer asked for.
        #
        # Below the floor the trim is an artefact of the screen's own one-decimal
        # precision rather than something the customer can act on — see
        # SLEEVE_CLAMP_DISCLOSURE_FLOOR_PCT. The sleeve is still placed at its
        # clamped size either way; only the disclosure is gated.
        clamp_floor = SLEEVE_CLAMP_DISCLOSURE_FLOOR_PCT / 100.0 * inp.total_corpus
        if requested_sleeve and (requested_sleeve - sized_sleeve) > clamp_floor:
            sleeve_clamped = True
        multi_asset_block = phase4_multi_asset(
            equities_amount=residual_equity_corpus_pre_multi_asset,
            debt_amount=debt_amount,
            others_amount=others_amount,
            composition=comp,
            requested_amount=sized_sleeve,
        )
    else:
        multi_asset_block = phase4_multi_asset(
            equities_amount=residual_equity_corpus_pre_multi_asset,
            debt_amount=debt_amount,
            others_amount=others_amount,
            composition=comp,
        )

    # R193: overflow redistribution. When the multi-asset others slice exceeds
    # the budgeted others_amount, the excess is split between equity (residual)
    # and debt (allocation_2_debt_pct-weighted, clamped to remaining debt
    # capacity after the multi-asset debt component).
    multi_asset_others_excess = max(
        0,
        multi_asset_block.others_component - others_amount,
    )
    debt_capacity_after_multi = max(
        0,
        debt_amount - multi_asset_block.debt_component,
    )
    if (
        multi_asset_others_excess > 0
        and (allocation_2_debt_pct + allocation_2_equity_pct) > 0
    ):
        # Spec wording: excess_to_debt = min(round_to_100(excess × allocation_2_debt
        # / 100), debt_amount − multi_asset_debt_component).
        excess_to_debt = min(
            round_to_100(multi_asset_others_excess * allocation_2_debt_pct / 100),
            debt_capacity_after_multi,
        )
        excess_to_equity = multi_asset_others_excess - excess_to_debt
    else:
        excess_to_debt = 0
        excess_to_equity = 0

    # R194: residual equity corpus AFTER multi-asset equity component AND the
    # excess-to-equity redirect.
    residual_equity_corpus_final = max(
        0,
        residual_equity_corpus_pre_multi_asset
        - multi_asset_block.equity_component
        - excess_to_equity,
    )

    # R217 (preview for Task 10): residual debt corpus after multi-asset debt
    # component AND the excess-to-debt redirect.
    residual_debt_corpus = max(
        0,
        debt_amount - multi_asset_block.debt_component - excess_to_debt,
    )

    # R196-R200: equity subgroup allocation via upstream phase5_equity_subgroups.
    # This already applies the sector/value view-<= 7 gates and the upstream
    # PHASE5_MIN_SUBGROUP_SHARE_PCT (2%) internal drop.
    # Only the EQUITY pins are placed here — gold and the debt bucket stay
    # residuals of their class (D-A5), and writing a pin in directly would
    # strand the rest of the class.
    equity_pins = {sg: amt for sg, amt in pins.items() if sg in EQUITY_SUBGROUPS}
    if equity_pins:
        # round_to_100 rounds half UP, so the sleeve's equity slice can land up
        # to ~₹100 past the room the pins left behind. Phase 5 REFUSES to
        # truncate a customer's number (it raises), so re-fit against the pool
        # that actually has to hold them.
        equity_pins, scaled = _fit_pins_to_room(
            equity_pins, residual_equity_corpus_final
        )
        pins_scaled = pins_scaled or scaled
    equity_excluded = frozenset(
        sg for sg in subgroup_excluded if sg in EQUITY_SUBGROUPS
    )
    initial_subgroup_amounts = phase5_equity_subgroups(
        total_equity_for_subgroups=residual_equity_corpus_final,
        score=inp.effective_risk_score,
        market_commentary=inp.market_commentary,
        requested_amounts=equity_pins or None,
        excluded=equity_excluded or None,
    )

    # R198-R215: v2 average-based slider via shared helper (single source of
    # truth across the ideal and practical engines).
    #
    # Practical-engine spec (Excel R204 column "% OF EQUITIES" =
    # allocation_2 × B194 / (B194 + B187 + B181)): the per-subgroup share % for
    # the slider's drop decision is taken against the TOTAL equity pool, not
    # just the MF residual. Total = residual_equity_corpus_final +
    # multi_asset_amount + non_mf_equity_actual (ELSS deliberately excluded —
    # frozen and treated separately).
    total_equity_pool_for_shares = (
        residual_equity_corpus_final
        + multi_asset_block.multi_asset_amount
        + non_mf_equity_actual
    )
    renormalised, min_equity_pct_required, average_equity_subgroup_allocation_pct = (
        apply_equity_subgroup_slider(
            initial_subgroup_amounts,
            equity_pool=residual_equity_corpus_final,
            equities_amount=equities_amount,
            locked_amount=elss_amount_frozen + non_mf_equity_actual,
            share_denominator=total_equity_pool_for_shares,
            # D-A1: the slider stops the ENGINE producing dust; a number the
            # customer typed is not the engine's to police.
            exempt=frozenset(equity_pins),
        )
    )

    # Pad with zeros so the dict stays exhaustive over EQUITY_SUBGROUPS.
    equity_subgroup_amounts: dict[str, int] = {sg: 0 for sg in EQUITY_SUBGROUPS}
    for sg, amt in renormalised.items():
        equity_subgroup_amounts[sg] = amt

    # Reconcile any residual rounding drift against residual_equity_corpus_final.
    # Never park it on a PINNED row — the customer's number has to come out
    # exactly as they typed it (D-A1) — so prefer an engine-filled row.
    drift = residual_equity_corpus_final - sum(equity_subgroup_amounts.values())
    if drift != 0:
        holders = {
            sg: amt
            for sg, amt in equity_subgroup_amounts.items()
            if amt > 0 and sg not in equity_pins
        }
        if not holders:
            # The slider measures its drop bar against the TOTAL equity pool
            # (share_denominator), so a pin large enough to leave only slivers
            # behind can put EVERY other candidate under the bar at once — the
            # slider then has no survivor to redistribute to and returns them
            # all as zero. What it freed is real corpus, not rounding noise,
            # and a pinned row must not absorb it (D-A1). Phase 5 always spends
            # the whole pool, so its pre-slider split IS exactly that freed
            # room: put the money back where the engine's own tilt had it.
            pre_slider_non_pinned = {
                sg: amt
                for sg, amt in initial_subgroup_amounts.items()
                if amt > 0 and sg not in equity_pins
            }
            if pre_slider_non_pinned:
                for sg, amt in pre_slider_non_pinned.items():
                    equity_subgroup_amounts[sg] = amt
                holders = pre_slider_non_pinned
                drift = residual_equity_corpus_final - sum(
                    equity_subgroup_amounts.values()
                )
    if drift != 0:
        # Genuine rounding noise (a handful of rupees), or the degenerate case
        # where no engine-filled equity row is holding money. Only then may a
        # pin be touched.
        #
        # A COMPLETE distribution (spec 2026-09-15) makes that degenerate case
        # the NORMAL one: every equity row is either pinned or sent as an
        # explicit zero, so there is never an engine-filled row to prefer. The
        # sleeve cannot take the money back either — it is spare equity
        # precisely BECAUSE a different class room clamped the sleeve, and
        # growing it would draw debt the customer's debt pins have claimed
        # (measured: absorbing ₹1,60,000 of spare equity costs the two debt
        # rows ₹55,400). So a pin must absorb it — but SPREAD in proportion,
        # never dumped whole on the largest row, which put one row 8.4% above
        # what the customer typed while its neighbours landed exactly.
        holders = holders or {
            sg: amt for sg, amt in equity_subgroup_amounts.items() if amt > 0
        }
        if holders:
            spread = _split_pro_rata(
                sum(holders.values()) + drift, {sg: amt for sg, amt in holders.items()}
            )
            equity_subgroup_amounts.update(spread)

    # R220-R222: gold / commodities = others budget minus what the multi-asset
    # fund's own others slice already absorbed (less any excess we already
    # redistributed to eq/debt).
    others_minus_multi = max(
        0,
        others_amount
        - (multi_asset_block.others_component - multi_asset_others_excess),
    )
    residual_other_corpus = round_to_100(others_minus_multi)

    # R217-R219: assemble the long-term subgroup_amounts dict, exhaustive
    # over STEP4_SUBGROUPS.
    long_term_subgroup_amounts: dict[str, int] = {sg: 0 for sg in STEP4_SUBGROUPS}
    long_term_subgroup_amounts["multi_asset"] = multi_asset_block.multi_asset_amount
    for sg, amt in equity_subgroup_amounts.items():
        long_term_subgroup_amounts[sg] = amt
    # Spec §B.5 step 11: the long-term debt residual routes to
    # arbitrage_plus_income BY DEFAULT; the tax-rate gate on debt routing
    # is the long-term threshold (asset_allocation Part A.4; medium removed 2026-09-24).
    #
    # D-A5 (spec 2026-09-14), AS AMENDED by spec 2026-09-15 §7: a debt pin is
    # still not written in directly — it sizes the sleeve and the rows are left
    # as the class residual — but the residual now splits PRO-RATA across every
    # debt row the customer NAMED instead of landing entirely in one.
    #
    # Why pro-rata is exact: with the carve-outs suspended (§3) long-term is the
    # whole corpus, so the customer's pure-debt rows sum to
    # `debt_amount - sleeve_debt_component` by the screen's own validation —
    # precisely the residual. Splitting it in proportion reproduces their
    # numbers. D-A5's "comes out at or ABOVE the ask" therefore stops being
    # true, deliberately: a named debt row now lands AT its ask, bar rounding.
    # Under the old single-row rule every other named row landed at ZERO and
    # nothing said so (measured: a 10% short_debt ask placing ₹0, with
    # sleeve_clamped=False, pins_scaled=False, shortfall_reason=None).
    #
    # D-B5 (spec 2026-09-14) is unchanged and applies FIRST. `_subgroup_pins`
    # never yields an excluded row, so an excluded row is never a participant
    # and its share redistributes across the rows that remain. The DEFAULT home
    # still reroutes when refused; only if every default home is refused is the
    # class homeless, and then the sleeve is the sole debt vehicle and whatever
    # it could not absorb is disclosed rather than silently placed.
    for sg in DEBT_SUBGROUPS:
        long_term_subgroup_amounts[sg] = 0
    named_debt = {sg: amt for sg, amt in pins.items() if sg in DEBT_SUBGROUPS and amt > 0}
    if named_debt:
        long_term_subgroup_amounts.update(
            _split_pro_rata(residual_debt_corpus, named_debt)
        )
    else:
        # `arbitrage` is deliberately absent from the default order: plain
        # arbitrage is a short/medium-term instrument, and Prozpr never routes
        # long-term money there on its own (§4).
        default_row = next(
            (sg for sg in DEBT_DEFAULT_ORDER if sg not in subgroup_excluded), None
        )
        if default_row is not None:
            long_term_subgroup_amounts[default_row] = residual_debt_corpus
    long_term_subgroup_amounts["gold_commodities"] = residual_other_corpus

    return _PracticalLongTermResult(
        total_long_term_corpus=total_long_term_corpus,
        min_equity_elss_pct=min_equity_elss_pct,
        phase1_bounds_allocation_1=bounds_1,
        practical_others_gate_fired=practical_others_gate_fired,
        allocation_2_equity_pct=allocation_2_equity_pct,
        allocation_2_debt_pct=allocation_2_debt_pct,
        allocation_2_others_pct=allocation_2_others_pct,
        equities_amount=equities_amount,
        debt_amount=debt_amount,
        others_amount=others_amount,
        elss_amount_frozen=elss_amount_frozen,
        max_non_mf_equity_pct_computed=max_non_mf_equity_pct_computed,
        max_non_mf_equity_pct_considered=max_non_mf_equity_pct_considered,
        max_equities_shares=max_equities_shares,
        non_mf_equity_actual=non_mf_equity_actual,
        excess_direct_stocks=excess_direct_stocks,
        residual_equity_corpus_pre_multi_asset=residual_equity_corpus_pre_multi_asset,
        multi_asset_block=multi_asset_block,
        multi_asset_others_excess=multi_asset_others_excess,
        excess_to_debt=excess_to_debt,
        excess_to_equity=excess_to_equity,
        residual_equity_corpus_final=residual_equity_corpus_final,
        residual_debt_corpus=residual_debt_corpus,
        average_equity_subgroup_allocation_pct=average_equity_subgroup_allocation_pct,
        min_equity_pct_required=min_equity_pct_required,
        equity_subgroup_amounts=equity_subgroup_amounts,
        initial_equity_subgroup_amounts=dict(initial_subgroup_amounts),
        equity_share_denominator=int(total_equity_pool_for_shares),
        residual_other_corpus=residual_other_corpus,
        long_term_subgroup_amounts=long_term_subgroup_amounts,
        goals_allocated=lt_goals,
        future_investment=future_investment,
        pins_scaled=pins_scaled,
        sleeve_clamped=sleeve_clamped,
    )


def run_practical_allocation(
    inp: PracticalAllocationInput,
    trace: Optional[dict] = None,
) -> PracticalAllocationOutput:
    """Holdings-aware goal-based allocation. Spec §B.4.

    Pipeline:
      1. ELSS freeze — subtract elss_corpus to get rebalancing_corpus.
      2. Build sub-AllocationInput with total_corpus = rebalancing_corpus.
      3. Run upstream steps 1-2 (emergency, short-term) verbatim (medium removed 2026-09-24).
      4. Convert a class preference from an OVERALL-portfolio ask into the
         long-term split, subtracting what steps 1-3 already committed
         (_lt_class_targets_from_overall) — spec 2026-09-14.
      5. Run _run_practical_long_term (Excel R157-R222) for the long-term step.
      6. Aggregate with step5_aggregation_with_frozen (adds two frozen rows).
      7. Assemble PracticalAllocationOutput.

    If `trace` is provided (any dict), populated in-place with intermediate
    state for downstream Excel-comparison views — never affects the return value.
    """
    rebalancing_corpus = inp.total_corpus - inp.elss_corpus
    if rebalancing_corpus < 0:
        # Edge case (α) per spec §B.7 — should never happen in practice.
        raise InfeasibleGoalError(
            f"ELSS corpus ({inp.elss_corpus}) exceeds total corpus ({inp.total_corpus})"
        )

    # Build a sub-AllocationInput with rebalancing_corpus as total_corpus.
    # model_dump() preserves all parent fields; we override total_corpus only.
    parent_fields = AllocationInput.model_fields.keys()
    sub_inp = AllocationInput(
        **{k: getattr(inp, k) for k in parent_fields if k != "total_corpus"},
        total_corpus=rebalancing_corpus,
    )

    prefs = inp.human_override
    # D-B4: ANY preference, class or sub-group. A customer who only excludes
    # gold must still get the commodity bound, or the sleeve would hand them
    # back the gold they refused.
    preference_set = prefs is not None and not prefs.is_empty()

    if preference_set:
        # Spec 2026-09-15 §3: a stated preference SUSPENDS the bucket carve-outs.
        # If the customer has told us where their money goes, the engine is not
        # also deciding to hold back an emergency reserve, a near-term goal pot,
        # or an NFA liability offset. Steps 1-3 are replaced with zeroed outputs
        # carrying the whole corpus forward, so the long-term step receives
        # `rebalancing_corpus` intact.
        #
        # Two things fall out of it: `_lt_class_targets_from_overall` becomes the
        # identity (nothing committed), and `_subgroup_pins` — which takes a raw
        # % of total_corpus with no subtraction of what steps 1-3 placed in the
        # same subgroups — can no longer double-count.
        #
        # Deliberately accepted: a goal under 24 months leaves the plan entirely
        # (step 4 only selects >= 24 months), and a leveraged customer loses the
        # liability offset. Both are disclosed — see human_override's
        # suspended-buffer reason and `carve_outs_at_risk` on the screen.
        s1, s2 = _no_carveout_buckets(rebalancing_corpus)
    else:
        s1 = step1_emergency.run(sub_inp)
        s2 = step2_short_term.run(sub_inp, s1.remaining_corpus)

    # Spec 2026-09-14 §4.1: the class preference targets the OVERALL portfolio.
    # Steps 1-2 have already committed rupees per class (emergency / short
    # buckets), so the long-term step is asked for the REMAINDER; a
    # class a bucket already over-holds gets 0 here and is disclosed downstream.
    requested_lt_class_pcts: Optional[dict[str, float]] = None
    if inp.human_override is not None and inp.human_override.asset_class_requested:
        requested_lt_class_pcts = _lt_class_targets_from_overall(
            inp.human_override.asset_class_requested,
            float(inp.total_corpus),
            _committed_by_class(s1, s2),
        )

    # Spec 2026-09-14 §5: a sub-group ask is an INPUT to the phase that decides
    # it, not a post-hoc reshape. Asks are stored as a share of the WHOLE
    # portfolio (D-A3), so a pin is one multiply against total_corpus — no
    # class amounts needed, and hence no pin-free probe run of the long-term
    # step to read them off. The third return value is exactly
    # `"gold_commodities" in excluded`, and the long-term step reads D-B3 off
    # the exclusion set it already receives — so it is not carried separately.
    subgroup_pins, subgroup_excluded, gold_excluded = _subgroup_pins(
        prefs, float(inp.total_corpus)
    )

    s4_practical = _run_practical_long_term(
        inp=sub_inp,
        remaining_corpus=s2.remaining_corpus,
        elss_amount=inp.elss_corpus,
        non_mf_equity_input=inp.non_mf_equity_corpus,
        nfa=inp.net_financial_assets,
        max_non_mf_equity_pct_client_input=inp.max_non_mf_equity_pct_client_input,
        requested_class_pcts=requested_lt_class_pcts,
        subgroup_pins=subgroup_pins,
        subgroup_excluded=subgroup_excluded,
        preference_set=preference_set,
    )

    s5 = _step5_aggregation_with_frozen(
        total_corpus=inp.total_corpus,
        s1=s1,
        s2=s2,
        s4_practical=s4_practical,
        elss_amount=inp.elss_corpus,
        non_mf_equity_actual=s4_practical.non_mf_equity_actual,
    )

    if trace is not None:
        trace.update(
            {
                "rebalancing_corpus": rebalancing_corpus,
                "lt_corpus_entering": s2.remaining_corpus,
                "inputs": {
                    "elss_corpus": float(inp.elss_corpus),
                    "non_mf_equity_corpus": float(inp.non_mf_equity_corpus),
                    "mf_corpus": float(inp.mf_corpus),
                    "net_financial_assets": (
                        None
                        if inp.net_financial_assets is None
                        else float(inp.net_financial_assets)
                    ),
                    "max_non_mf_equity_pct_client_input": inp.max_non_mf_equity_pct_client_input,
                },
                "step1_emergency": s1.model_dump(mode="json"),
                "step2_short_term": s2.model_dump(mode="json"),
                "step4_long_term": _practical_lt_result_to_dict(s4_practical),
                "step5_aggregation": s5.model_dump(mode="json"),
            }
        )

    built = _build_output(inp, s1, s2, s4_practical, s5)
    reshaped, applied = apply_human_override(
        built,
        inp.human_override,
        inp.multi_asset_composition,
        # D-A4: the engine scaled over-subscribed pins down to fit their class;
        # the customer has to be told, so carry the flag into the disclosure.
        pins_scaled=s4_practical.pins_scaled,
        sleeve_clamped=s4_practical.sleeve_clamped,
        # Spec 2026-09-15 §9: what the carve-out suspension actually cost this
        # customer. Empty when nothing was at risk, and nothing is said.
        carve_outs_suspended=carve_outs_at_risk(inp) if preference_set else [],
    )
    result = built if applied is None else reshaped.model_copy(
        update={"human_override_applied": applied}
    )
    # Last step (spec 2026-09-21): keep the current amount where the proposed
    # move is under threshold, so the customer is not churned for a tiny drift.
    return apply_current_allocation_snap(
        result, inp.current_subgroup_allocation, inp.total_corpus
    )


def _practical_lt_result_to_dict(r: _PracticalLongTermResult) -> dict:
    """JSON-serializable view of the long-term step's internal state.

    Field names map 1:1 to the dataclass; values are unwrapped to plain
    Python primitives so the trace can be JSON-dumped.
    """
    return {
        # Excel R157-R165 (Task 5 — corpus, ELSS floor, phase-1 bounds)
        "total_long_term_corpus": r.total_long_term_corpus,
        "min_equity_elss_pct": r.min_equity_elss_pct,
        "phase1_bounds_allocation_1": {
            "eq_min": r.phase1_bounds_allocation_1.eq_min,
            "eq_max": r.phase1_bounds_allocation_1.eq_max,
            "debt_min": r.phase1_bounds_allocation_1.debt_min,
            "debt_max": r.phase1_bounds_allocation_1.debt_max,
            "others_min": r.phase1_bounds_allocation_1.others_min,
            "others_max": r.phase1_bounds_allocation_1.others_max,
        },
        # R167-R174 (Task 6 — others-gate, allocation_2 percentages)
        "practical_others_gate_fired": r.practical_others_gate_fired,
        "allocation_2_equity_pct": r.allocation_2_equity_pct,
        "allocation_2_debt_pct": r.allocation_2_debt_pct,
        "allocation_2_others_pct": r.allocation_2_others_pct,
        # R177-R186 (Task 7 — amounts, ELSS, non-MF cap, residual_equity)
        "equities_amount": r.equities_amount,
        "debt_amount": r.debt_amount,
        "others_amount": r.others_amount,
        "elss_amount_frozen": r.elss_amount_frozen,
        "max_non_mf_equity_pct_computed": r.max_non_mf_equity_pct_computed,
        "max_non_mf_equity_pct_considered": r.max_non_mf_equity_pct_considered,
        "max_equities_shares": r.max_equities_shares,
        "non_mf_equity_actual": r.non_mf_equity_actual,
        "excess_direct_stocks": r.excess_direct_stocks,
        "residual_equity_corpus_pre_multi_asset": r.residual_equity_corpus_pre_multi_asset,
        # R187-R194 (Task 8 — multi-asset block & overflow)
        "multi_asset_block": r.multi_asset_block.model_dump(mode="json"),
        "multi_asset_others_excess": r.multi_asset_others_excess,
        "excess_to_debt": r.excess_to_debt,
        "excess_to_equity": r.excess_to_equity,
        "residual_equity_corpus_final": r.residual_equity_corpus_final,
        "residual_debt_corpus": r.residual_debt_corpus,
        # R196-R215 (Task 9 — equity subgroup slider)
        "average_equity_subgroup_allocation_pct": r.average_equity_subgroup_allocation_pct,
        "min_equity_pct_required": r.min_equity_pct_required,
        "equity_subgroup_amounts": dict(r.equity_subgroup_amounts),
        "initial_equity_subgroup_amounts": dict(r.initial_equity_subgroup_amounts),
        "equity_share_denominator": r.equity_share_denominator,
        # R217-R222 (Task 10 — debt & others residuals)
        "residual_other_corpus": r.residual_other_corpus,
        "long_term_subgroup_amounts": dict(r.long_term_subgroup_amounts),
        "pins_scaled": r.pins_scaled,
        "sleeve_clamped": r.sleeve_clamped,
        "goals_allocated": [g.model_dump(mode="json") for g in r.goals_allocated],
        "future_investment": (
            r.future_investment.model_dump(mode="json")
            if r.future_investment is not None
            else None
        ),
    }


def _adapt_practical_to_step4_output(
    s4_practical: _PracticalLongTermResult,
) -> Step4Output:
    """Build a Step4Output whose subgroup_amounts is the practical long-term
    distribution. asset_allocation_pydantic.step5_aggregation only reads
    .subgroup_amounts on the step4 input, so the other fields are best-effort
    placeholders. We construct minimal valid pydantic objects."""
    zero_alloc = AssetClassAllocation(
        equities_pct=0,
        debt_pct=0,
        others_pct=0,
        equities_amount=s4_practical.equities_amount,
        debt_amount=s4_practical.debt_amount,
        others_amount=s4_practical.others_amount,
    )
    return Step4Output(
        asset_class_allocation=zero_alloc,
        planned_asset_class_allocation=zero_alloc,
        planned_subgroup_amounts=s4_practical.long_term_subgroup_amounts,
        multi_asset=s4_practical.multi_asset_block,
        goals_allocated=s4_practical.goals_allocated,
        leftover_corpus=0,
        total_long_term_corpus=s4_practical.total_long_term_corpus,
        total_allocated=sum(s4_practical.long_term_subgroup_amounts.values()),
        remaining_corpus=0,
        future_investment=s4_practical.future_investment,
        subgroup_amounts=s4_practical.long_term_subgroup_amounts,
    )


def _step5_aggregation_with_frozen(
    *,
    total_corpus: float,
    s1: Step1Output,
    s2: Step2Output,
    s4_practical: _PracticalLongTermResult,
    elss_amount: float,
    non_mf_equity_actual: int,
) -> Step5Output:
    """Wraps upstream step5_aggregation.run and appends two frozen subgroup
    rows: tax_efficient_equities (ELSS) and non_mf_equities (non-MF actual).

    grand_total reconciles to total_corpus (NOT rebalancing_corpus) because
    the two frozen rows make ELSS and non-MF actual visible.
    """
    s4_adapter = _adapt_practical_to_step4_output(s4_practical)
    # Call upstream against total_corpus, not rebalancing_corpus, so the
    # match-flag uses the correct denominator. The upstream function does not
    # subtract anything; it just sums the four bucket dicts.
    base = step5_aggregation.run(total_corpus, s1, s2, s4_adapter)

    rows = list(base.rows)
    elss_int = int(round(elss_amount))
    if elss_int > 0:
        rows.append(
            AggregatedRow(
                subgroup="tax_efficient_equities",
                emergency=0,
                short_term=0,
                medium_term=0,
                long_term=elss_int,
                total=elss_int,
            )
        )
    if non_mf_equity_actual > 0:
        rows.append(
            AggregatedRow(
                subgroup="non_mf_equities",
                emergency=0,
                short_term=0,
                medium_term=0,
                long_term=non_mf_equity_actual,
                total=non_mf_equity_actual,
            )
        )

    grand_total = sum(row.total for row in rows)
    grand_total_matches_corpus = abs(grand_total - round_to_100(total_corpus)) <= 500

    return Step5Output(
        rows=rows,
        grand_total=grand_total,
        grand_total_matches_corpus=grand_total_matches_corpus,
    )


def _build_asset_class_breakdown(
    inp: PracticalAllocationInput,
    s1: Step1Output,
    s2: Step2Output,
    s4_practical: _PracticalLongTermResult,
) -> AssetClassBreakdown:
    """Roll up subgroup amounts to (equity, debt, others) per bucket and
    overall. Mirrors what step7_presentation does in asset_allocation_pydantic
    but inlined here so we don't pull in that file's LLM rationale plumbing.

    Multi-asset subgroup amounts are carved into equity/debt/others using
    ``inp.multi_asset_composition`` (defaults 65/25/10) before rollup, matching
    the ideal engine's step7_presentation behaviour. Without this carve, the
    full multi-asset amount would land in equity (via SUBGROUP_TO_ASSET_CLASS),
    overstating equity and understating debt/others at the bucket level.

    tax_efficient_equities and non_mf_equities are added as equity in the
    long_term bucket via the practical-side rollup.
    """
    # Long-term: include the frozen ELSS + non-MF as equity (they ARE equity
    # exposure, just not via MF subgroups in the allocation_pydantic dict).
    lt_subs = dict(s4_practical.long_term_subgroup_amounts)
    lt_subs["tax_efficient_equities"] = s4_practical.elss_amount_frozen
    lt_subs["non_mf_equities"] = s4_practical.non_mf_equity_actual

    bucket_dicts = {
        "emergency": s1.subgroup_amounts,
        "short_term": s2.subgroup_amounts,
        "long_term": lt_subs,
    }

    # SUBGROUP_TO_ASSET_CLASS doesn't have the two practical-only subgroups;
    # add them locally as equity.
    extended_map = dict(SUBGROUP_TO_ASSET_CLASS)
    extended_map["tax_efficient_equities"] = "equity"
    extended_map["non_mf_equities"] = "equity"

    comp = inp.multi_asset_composition

    def split_with(subs: dict[str, int]) -> tuple[int, int, int]:
        eq = dt = oth = 0
        for sg, amt in subs.items():
            if sg == "multi_asset" and amt > 0:
                # Carve the multi-asset slice into its true eq/dt/oth components
                # (matches step7_presentation._asset_class_breakdown). Round
                # equity and others, give debt the residual so the three pieces
                # sum exactly to amt.
                eq_part = int(round(amt * comp.equity_pct / 100.0))
                oth_part = int(round(amt * comp.others_pct / 100.0))
                dt_part = amt - eq_part - oth_part
                eq += eq_part
                dt += dt_part
                oth += oth_part
                continue
            cls = extended_map.get(sg, "others")
            if cls == "equity":
                eq += amt
            elif cls == "debt":
                dt += amt
            else:
                oth += amt
        return eq, dt, oth

    per_bucket: List[BucketAssetClassSplit] = []
    for bucket_name, subs in bucket_dicts.items():
        eq, dt, oth = split_with(subs)
        tot = eq + dt + oth
        per_bucket.append(
            BucketAssetClassSplit(
                bucket=bucket_name,  # type: ignore[arg-type]
                equity=eq,
                debt=dt,
                others=oth,
                equity_pct=(eq * 100.0 / tot) if tot else 0.0,
                debt_pct=(dt * 100.0 / tot) if tot else 0.0,
                others_pct=(oth * 100.0 / tot) if tot else 0.0,
            )
        )

    eq_total = sum(b.equity for b in per_bucket)
    dt_total = sum(b.debt for b in per_bucket)
    oth_total = sum(b.others for b in per_bucket)
    grand = eq_total + dt_total + oth_total

    block = AssetClassSplitBlock(
        per_bucket=per_bucket,
        equity_total=eq_total,
        debt_total=dt_total,
        others_total=oth_total,
        equity_total_pct=(eq_total * 100.0 / grand) if grand else 0.0,
        debt_total_pct=(dt_total * 100.0 / grand) if grand else 0.0,
        others_total_pct=(oth_total * 100.0 / grand) if grand else 0.0,
    )

    # Bucket-keyed subgroup block, matching what the ideal engine emits via
    # step7_presentation._subgroup_breakdown. The practical engine has no
    # separate planned/recommended split, so both lists carry identical data.
    # Long-term picks up the two frozen practical-only rows so they appear in
    # the structured block too (not just in aggregated_subgroups).
    def _subgroup_bucket(bucket: str, amounts: dict[str, int]) -> SubgroupBucketSplit:
        total = sum(amounts.values())
        rows = [
            SubgroupBucketAllocation(
                subgroup=sg,
                amount=amt,
                pct_of_bucket=(amt * 100.0 / total) if total else 0.0,
            )
            for sg, amt in amounts.items()
            if amt > 0
        ]
        return SubgroupBucketSplit(
            bucket=bucket,  # type: ignore[arg-type]
            subgroups=rows,
        )

    buckets_block = [
        _subgroup_bucket("emergency", s1.subgroup_amounts),
        _subgroup_bucket("short_term", s2.subgroup_amounts),
        _subgroup_bucket("long_term", lt_subs),
    ]
    subgroups_block = SubgroupBreakdown(
        planned=buckets_block,
        recommended=buckets_block,
    )

    return AssetClassBreakdown(
        planned=block,
        recommended=block,  # practical engine has no separate planned/recommended split
        recommended_sum_matches_grand_total=True,
        subgroups=subgroups_block,
    )


def _build_output(
    inp: PracticalAllocationInput,
    s1: Step1Output,
    s2: Step2Output,
    s4_practical: _PracticalLongTermResult,
    s5: Step5Output,
) -> PracticalAllocationOutput:
    """Assemble the seven shared fields + corpus_breakdown."""

    # 1. client_summary
    client_summary = ClientSummary(
        age=inp.age,
        occupation=inp.occupation_type,
        effective_risk_score=inp.effective_risk_score,
        total_corpus=inp.total_corpus,
        goals=inp.goals,
        emergency_fund_months=s1.emergency_fund_months,
        monthly_household_expense=inp.monthly_household_expense,
    )

    # 2. bucket_allocations
    emergency_bucket = BucketAllocation(
        bucket="emergency",
        goals=[],
        total_goal_amount=s1.total_emergency,
        allocated_amount=s1.total_emergency,
        future_investment=s1.future_investment,
        subgroup_amounts=s1.subgroup_amounts,
    )
    short_bucket = BucketAllocation(
        bucket="short_term",
        goals=s2.goals_allocated,
        total_goal_amount=s2.total_goal_amount,
        allocated_amount=s2.allocated_amount,
        future_investment=s2.future_investment,
        subgroup_amounts=s2.subgroup_amounts,
    )
    long_bucket = BucketAllocation(
        bucket="long_term",
        goals=s4_practical.goals_allocated,
        total_goal_amount=round_to_100(
            sum(g.amount_needed for g in s4_practical.goals_allocated),
        ),
        allocated_amount=sum(s4_practical.long_term_subgroup_amounts.values()),
        future_investment=s4_practical.future_investment,
        subgroup_amounts=s4_practical.long_term_subgroup_amounts,
    )

    # 3. aggregated_subgroups — convert Step5Output.rows to AggregatedSubgroupRow.
    aggregated = [
        AggregatedSubgroupRow(
            subgroup=row.subgroup,
            emergency=float(row.emergency),
            short_term=float(row.short_term),
            medium_term=float(row.medium_term),
            long_term=float(row.long_term),
            total=float(row.total),
        )
        for row in s5.rows
    ]

    # 4. future_investments_summary
    future_summary: List[FutureInvestment] = []
    for step_out in (s1, s2):
        if step_out.future_investment is not None:
            future_summary.append(step_out.future_investment)
    if s4_practical.future_investment is not None:
        future_summary.append(s4_practical.future_investment)

    # 5. grand_total, 6. all_amounts_in_multiples_of_100
    grand_total = float(s5.grand_total)
    all_mult_100 = all(
        v % 100 == 0
        for d in (
            s1.subgroup_amounts,
            s2.subgroup_amounts,
            s4_practical.long_term_subgroup_amounts,
        )
        for v in d.values()
    )

    # 7. asset_class_breakdown
    asset_class_breakdown = _build_asset_class_breakdown(
        inp,
        s1,
        s2,
        s4_practical,
    )

    # corpus_breakdown extras
    corpus_breakdown = CorpusBreakdown(
        total_corpus_inr=int(round(inp.total_corpus)),
        mf_corpus_inr=int(round(inp.mf_corpus)),
        non_mf_equity_input_inr=int(round(inp.non_mf_equity_corpus)),
        elss_corpus_inr=int(round(inp.elss_corpus)),
        rebalancing_corpus_inr=int(round(inp.total_corpus - inp.elss_corpus)),
        non_mf_equity_actual_inr=s4_practical.non_mf_equity_actual,
        excess_direct_stocks_inr=s4_practical.excess_direct_stocks,
        max_non_mf_equity_pct_computed=s4_practical.max_non_mf_equity_pct_considered,
        lt_equities_amount_inr=s4_practical.equities_amount,
        non_mf_equity_cap_inr=int(
            round(
                s4_practical.max_non_mf_equity_pct_considered
                * s4_practical.equities_amount,
            )
        ),
    )

    return PracticalAllocationOutput(
        client_summary=client_summary,
        bucket_allocations=[emergency_bucket, short_bucket, long_bucket],
        aggregated_subgroups=aggregated,
        future_investments_summary=future_summary,
        grand_total=grand_total,
        all_amounts_in_multiples_of_100=all_mult_100,
        asset_class_breakdown=asset_class_breakdown,
        corpus_breakdown=corpus_breakdown,
    )
