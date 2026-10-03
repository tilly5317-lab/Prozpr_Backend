"""Additional-investment orchestrator.

Mirrors ``rebal_engine.service.compute_rebalancing_result``: primes the
practical (holdings-aware) allocation, materialises the engine input, runs the
pure additional-investment engine on a worker thread, and builds the chat facts
pack. Persistence is gated behind ``persist`` (left OFF in Plan 3a; Plan 3b
flips the default and wires ``persist_additional_investment_recommendation``).

The additional-investment engine follows the *allocation* I/O family — money is
plain ``float`` and the wrappers are ``AdditionalInvestmentInput`` /
``AdditionalInvestmentOutput`` (NOT Rebalancing's ``Decimal`` +
``ComputeRequest``/``Response``) — there is no tax-lot arithmetic here.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.domains.ai_engine.turn_context import TurnContext
    from practical_asset_allocation.pipeline import (  # type: ignore[import-not-found]
        PracticalAllocationOutput,
    )

from app.domains.ai_engine.common import ensure_ai_agents_path, trace_line
from app.domains.additional_investment.services.ainv_engine.holdings_snapshot import (
    HoldingsSnapshot,
    load_holdings_snapshot,
)
from app.domains.additional_investment.services.ainv_engine.input_builder import (
    build_additional_investment_input_for_user,
    goal_share_for,
)
from app.domains.mutual_funds.services.scheme_classification import (
    SHORT_TERM_HOLDING_SUBGROUPS,
    short_term_holdings_total,
)
from app.domains.practical_asset_allocation.services.paa_engine.input_builder import (
    CorpusPin,
)
from app.domains.practical_asset_allocation.services.paa_engine.service import (
    compute_practical_allocation_result,
)
from app.domains.practical_asset_allocation.services.practical_allocation_persist_service import (
    persist_practical_allocation_run,
)
from app.domains.cashflow.services.cashflow_persist_service import (
    mark_stale as mark_cashflow_stale,
)
from app.domains.profile.services.personal_finance_write_service import (
    set_starting_monthly_investment,
)
from app.domains.additional_investment.services.additional_investment_persist_service import (
    persist_additional_investment_recommendation,
)
from app.domains.profile.services.preference_tagging import (
    preference_id_for,
)
from app.domains.rebalancing.services.rebalancing_read_service import (
    latest_buy_trades_by_subgroup,
)

ensure_ai_agents_path()

from additional_investment.models import (  # type: ignore[import-not-found]  # noqa: E402
    AdditionalInvestmentOutput,
    Cadence,
)
from additional_investment.pipeline import (  # type: ignore[import-not-found]  # noqa: E402
    run_additional_investment,
)


logger = logging.getLogger(__name__)


_MSG_ENGINE_ERROR = (
    "I couldn't work out where to invest your money right now. Try again in a "
    "moment, and if it keeps happening let us know via the help option."
)

# Notional corpus for the sized allocation that supplies a SIP's long-term split
# when the real plan's long-term column is under _SIP_MIN_LONG_TERM_COLUMN_INR
# (e.g. no-CAMS, corpus ≈ 0). Long-term ratios are scale-invariant, so the exact
# figure doesn't matter.
_SIP_RATIO_SIZING_CORPUS_INR = 10_000_000.0  # ₹1 crore

# A SIP's long-term share needs a long-term plan at least this big to split by;
# below it the ratios come from a sized allocation instead.
_SIP_MIN_LONG_TERM_COLUMN_INR = 10_000.0


def _long_term_column_inr(inp) -> float:
    return sum(r.long_term for r in inp.subgroups if r.subgroup not in inp.exclude_subgroups)


# Only the held short-term money the goals use is removed; the rest stays current.
def _long_term_holdings(
    by_subgroup: dict[str, float], held_for_goals: float
) -> dict[str, float]:
    short_total = short_term_holdings_total(by_subgroup)
    keep = (1.0 - min(held_for_goals, short_total) / short_total) if short_total > 0 else 1.0
    return {
        sg: v * keep if sg in SHORT_TERM_HOLDING_SUBGROUPS else v
        for sg, v in by_subgroup.items()
    }


# Stamped onto every persisted AdditionalInvestmentRun.engine_version. Bump when
# the additional-investment engine's output contract changes.
# 2.0.0: lumpsum deployments switched from single-bucket targeting to
# holdings-aware deficit fill (spec 2026-07-03); SIP unchanged.
# 3.0.0: SIP selection mirrors the latest persisted rebalancing run's BUY
# funds (equal split, rank-1 fallback, no caps) — spec 2026-07-05.
# 3.1.0: SIP per-fund cap re-introduced as max(cap_pct × monthly amount,
# AINV_SIP_FUND_CAP_FLOOR_INR); overflow walks down the ranking — amendment
# 2026-07-06.
# 3.2.0: lumpsum per-fund cap floored at AINV_LUMPSUM_FUND_CAP_FLOOR_INR
# (both deficit-fill and legacy modes) — same amendment.
# 3.3.0: a SIP whose plan was shaped by a stated preference targets the
# long-term column — the stated split — instead of the nearest unfunded goal
# (spec 2026-09-20).
# 3.4.0: top-1/2 funds per subgroup by corpus; per-fund cap and SIP mirror retired.
# 3.5.0: FY-end horizon anchoring live — the short-goal funding boundary counts to
# the financial-year end (months_to_fy_end), matching the allocation engine.
# 3.6.0: SIP-first goal waterfall — goal money first from the practical
# allocation's goal_funding (no cashflow projection); the rest follows the
# long-term plan; lumpsum deficits exclude the held short-term money the goals use.
# A preference plan has no goal_funding, so its SIP follows the stated split.
# 3.7.0: a lumpsum funds the short-term goals' full remaining need first — its
# practical run assumes no future SIP.
# Direct stocks are not part of any corpus.
# 3.8.0: a subgroup's amount rounds to ₹100 once, then splits across its funds,
# so two funds total the same as one.
AINV_ENGINE_VERSION = "ainv-3.8.0"

# Sentinel: derive the preference FK from `preference_id_for` (existing
# behaviour) unless the caller names the row that shaped the run (a chat
# candidate, or None) — mirrors rebalancing_persist_service.DERIVE_PREFERENCE_ID.
DERIVE_PREFERENCE_ID = object()


@dataclass(frozen=True)
class AdditionalInvestmentRunOutcome:
    """Immutable outcome of one additional-investment orchestration run.

    On the happy path ``output`` is set and ``blocking_message`` is None. When the
    input builder or a pre-check fails, ``output`` is None and ``blocking_message``
    carries the customer-facing gate text — the chat handler relays it via
    ``format_relay_or_canned`` instead of formatting a BUY list (so the
    orchestrator never raises on a gate). The chat handler builds the
    LLM facts pack itself from ``output`` at format time, so the orchestrator does
    not carry one.

    ``run_id`` is None whenever ``persist=False`` (the only mode in Plan 3a);
    Plan 3b flips ``persist`` and fills it with the persisted run's id.
    """

    output: "AdditionalInvestmentOutput | None"
    run_id: "uuid.UUID | None" = None
    blocking_message: str | None = None
    # Deficit-fill facts for the chat formatter (lumpsum only): one row per
    # deployed subgroup {subgroup, ideal_inr, current_inr, gap_inr, buy_inr, goal_row}.
    # None on the SIP path.
    deficit_facts: "list[dict] | None" = None
    # The practical (holdings-aware) allocation the run was built from —
    # human_override_applied lives on it. Set on the SUCCESS path only; a
    # blocking outcome carries no plan to read a preference off.
    practical_result: "PracticalAllocationOutput | None" = None


async def compute_additional_investment_result(
    user,
    user_question: str,
    *,
    db: AsyncSession,
    acting_user_id: uuid.UUID,
    chat_session_id: Optional[uuid.UUID],
    deploy_amount_inr: float,
    cadence: Cadence,
    chat_ctx: "TurnContext",
    persist: bool = False,
    focus_category: Optional[str] = None,
    saved_investment_preference_id=DERIVE_PREFERENCE_ID,
    origin: Optional[str] = None,
    progress: Optional[Callable[[float, str], Awaitable[None]]] = None,
) -> AdditionalInvestmentRunOutcome:
    """Prime allocation → build input → run the engine.

    ``progress`` (optional) is awaited at each real stage boundary with
    (percent, customer-facing message) — the Invest page's SIP create endpoint
    passes a writer so its progress poller can show the live pipeline stage.
    The chat path passes nothing and is unchanged.

    Mirrors ``compute_rebalancing_result``: the practical allocation is primed
    first (its ``aggregated_subgroups`` feed the per-subgroup deploy split; the
    per-fund caps key off the deploy amount, so no corpus total is read), the
    engine input is materialised from that allocation, and the pure engine runs on
    a worker thread. Lumpsum pins the corpus and held short-term money to one
    holdings snapshot; SIP reads held short-term money off the preloaded user. The
    chat handler builds the LLM facts pack from the returned output at format time.

    Persistence is gated behind ``persist`` (False in Plan 3a; Plan 3b flips the
    default and calls ``persist_additional_investment_recommendation``).

    When ``persist`` and ``cadence is SIP_MONTHLY``, the deploy amount is also
    written to the canonical ``starting_monthly_investment`` — so a SIP the
    customer sets in chat shows up on the Invest page AND in their goal plan.
    This is the single place that sync happens; both callers (chat handler and the
    Invest-page create service) get it, and both own the commit.

    ``saved_investment_preference_id`` left at its default derives the FK from
    the active row via ``preference_id_for``; passing a UUID or None (a chat
    candidate row, or none) stamps that value verbatim on both persists.
    """
    trace_line("module: additional_investment — start")

    # Stage messages are customer-facing: describe the benefit, never the
    # mechanics (no engine/strategy internals — ranks, mirrors, caps).
    if progress:
        await progress(8, "Reading your profile & goals…")

    # Deficit fill (spec 2026-07-03), lumpsum only: the ideal is PAA at actual
    # holdings + fresh money. Lumpsum pins the corpus, held short-term money and the
    # deficit's `current` side to one holdings snapshot, so current_value staleness
    # cancels; SIP reads held short-term money off the preloaded user. No fallback
    # by product decision (2026-07-04, CAMS upload mandatory): a snapshot failure
    # propagates.
    snapshot: HoldingsSnapshot | None = None
    corpus_pin: CorpusPin | None = None
    if cadence is Cadence.LUMPSUM:
        snapshot = await load_holdings_snapshot(db, acting_user_id)
        corpus_pin = CorpusPin(
            total_corpus=snapshot.total_inr + deploy_amount_inr,
            elss_corpus=snapshot.elss_inr,
        )
        trace_line(
            f"additional_investment holdings snapshot: total={snapshot.total_inr}, "
            f"elss={snapshot.elss_inr}, unknown={snapshot.unknown_inr}"
        )

    if progress:
        await progress(18, "Designing your personalised mix…")

    paa_outcome = await compute_practical_allocation_result(
        user,
        user_question,
        chat_ctx=chat_ctx,
        corpus_pin=corpus_pin,
        monthly_sip=deploy_amount_inr if cadence is Cadence.SIP_MONTHLY else 0.0,
        short_term_holdings=(
            short_term_holdings_total(snapshot.by_subgroup) if snapshot is not None else None
        ),
    )
    if paa_outcome.result is None:
        # Pre-check failed (practical allocation could not be produced): return a
        # blocking outcome the chat handler relays via format_relay_or_canned —
        # never an engine BUY list, never a raise.
        return AdditionalInvestmentRunOutcome(
            output=None,
            blocking_message=paa_outcome.blocking_message or _MSG_ENGINE_ERROR,
        )

    # Investable corpus that decides 1 vs 2 funds per subgroup, off the practical
    # result already computed for this cadence (lumpsum's ran on corpus + deploy).
    # The sized SIP below rebuilds on a NOTIONAL corpus, so capture the real figure
    # here — never re-read it off the notional-sized result.
    investable_corpus_inr = float(paa_outcome.result.corpus_breakdown.total_corpus_inr)
    funding = getattr(paa_outcome.result, "goal_funding", None)
    goal_share_inr, goal_subgroup = goal_share_for(funding, cadence, deploy_amount_inr)
    held_for_goals = sum(g.from_holdings for g in funding.goals) if funding is not None else 0.0
    current_lt = (
        _long_term_holdings(snapshot.by_subgroup, held_for_goals) if snapshot is not None else None
    )

    # The latest rebalancing run is read only for the audit linkage
    # (sip_rebal_run_id below). Since spec 2026-09-24 the SIP no longer mirrors
    # that run — it deploys top-1/2 per subgroup by corpus like the lumpsum.
    # Best-effort: a read failure just drops the linkage, never gates.
    rebal_run_id: Optional[uuid.UUID] = None
    if cadence is Cadence.SIP_MONTHLY:
        try:
            rebal = await latest_buy_trades_by_subgroup(db, acting_user_id)
        except Exception:  # noqa: BLE001 — degrade, never gate
            logger.exception(
                "additional_investment: latest rebalancing-run read failed — "
                "SIP proceeds without the rebalancing-run audit linkage"
            )
            rebal = None
        if rebal is not None:
            rebal_run_id, _ = rebal
            trace_line(
                f"additional_investment SIP audit-linked to rebalancing run {rebal_run_id}"
            )

    if progress:
        await progress(60, "Shortlisting funds for you…")

    try:
        inp, debug = await build_additional_investment_input_for_user(
            paa_outcome.result,
            deploy_amount_inr=deploy_amount_inr,
            cadence=cadence,
            current_value_by_subgroup=current_lt,
            investable_corpus_inr=investable_corpus_inr,
            goal_share_inr=goal_share_inr,
            goal_subgroup=goal_subgroup,
        )
    except Exception:  # noqa: BLE001 — any builder failure → generic gate
        logger.exception("additional_investment input build failed")
        return AdditionalInvestmentRunOutcome(
            output=None, blocking_message=_MSG_ENGINE_ERROR
        )

    # The allocation the CHAT FACTS narrate — the sized run below reassigns this so
    # the reply never describes a plan that was discarded.
    effective_result = paa_outcome.result
    if (
        cadence is Cadence.SIP_MONTHLY
        and deploy_amount_inr - goal_share_inr > 0
        and _long_term_column_inr(inp) < _SIP_MIN_LONG_TERM_COLUMN_INR
    ):
        try:
            sized = await compute_practical_allocation_result(
                user,
                user_question,
                chat_ctx=chat_ctx,
                corpus_pin=CorpusPin(
                    total_corpus=_SIP_RATIO_SIZING_CORPUS_INR,
                    elss_corpus=0.0,
                ),
                monthly_sip=deploy_amount_inr,
            )
            if sized.result is not None:
                inp, debug = await build_additional_investment_input_for_user(
                    sized.result,
                    deploy_amount_inr=deploy_amount_inr,
                    cadence=cadence,
                    current_value_by_subgroup=None,
                    investable_corpus_inr=investable_corpus_inr,
                    goal_share_inr=goal_share_inr,
                    goal_subgroup=goal_subgroup,
                )
                effective_result = sized.result
                trace_line("additional_investment SIP long-term split taken from a sized allocation")
        except Exception:  # noqa: BLE001 — keep the real-corpus split, never raise
            logger.exception(
                "additional_investment: sized SIP long-term split failed — keeping "
                "the real-corpus split"
            )

    trace_line(f"additional_investment input debug: {debug}")

    if progress:
        await progress(75, "Allocating your monthly amount…")

    try:
        response: AdditionalInvestmentOutput = await asyncio.to_thread(
            run_additional_investment,
            inp,
        )
    except Exception:  # noqa: BLE001 — engine failure → generic gate, never a raise
        logger.exception("additional_investment engine run failed")
        return AdditionalInvestmentRunOutcome(
            output=None, blocking_message=_MSG_ENGINE_ERROR
        )

    # Deficit-fill facts for the chat formatter (lumpsum only): ideal vs current
    # vs deployed per subgroup, so the reply can narrate WHERE the gaps were.
    deficit_facts: list[dict] | None = None
    if current_lt is not None:
        rows_by = {r.subgroup: r for r in paa_outcome.result.aggregated_subgroups}
        buys_by: dict[str, float] = {}
        for b in response.buys:
            buys_by[b.asset_subgroup] = buys_by.get(b.asset_subgroup, 0.0) + float(b.amount_inr)
        deficit_facts = []
        for t in response.per_subgroup_target:
            row = rows_by.get(t.subgroup)
            ideal = float(row.total - row.short_term) if row is not None else 0.0
            current = current_lt.get(t.subgroup, 0.0)
            goal_row = (
                funding is not None
                and funding.allocated_amount > 0
                and t.subgroup == funding.asset_subgroup
            )
            if goal_row:
                ideal += funding.allocated_amount
                current += held_for_goals
            deficit_facts.append(
                {
                    "subgroup": t.subgroup,
                    "ideal_inr": ideal,
                    "current_inr": current,
                    "gap_inr": max(0.0, ideal - current),
                    "buy_inr": buys_by.get(t.subgroup, 0.0),
                    "goal_row": goal_row,
                }
            )

    # Persist the BUY-only recommendation whenever persist=True (persist=False
    # counterfactual paths skip the write). A null chat_session_id is allowed —
    # the Invest-page create endpoint persists an unlinked run. Money stays float
    # — persist writes Numeric(18,2) directly.
    #
    # source_allocation_run_id (Option B): the practical allocation run the deploy
    # is derived from. compute_practical_allocation_result returns no run id, so we
    # persist the practical run inline here to capture it — the only practical
    # persist in the ainv path (it does not route through paa_engine/chat.py), so
    # no double-write. The id is always produced, so the FK column is NOT NULL.

    # Mode + category metadata merged over the engine-input dump at persist
    # time (spec 2026-07-03 / 2026-07-04). None when there is nothing to add.
    request_extras: Optional[dict] = None
    _extras: dict = {}
    if snapshot is not None:
        _extras["deployment_mode"] = "deficit_fill"
        _extras["base_corpus_inr"] = snapshot.total_inr
        # Persist the per-subgroup ideal/current/gap/buy facts alongside the run
        # so the Invest-page lumpsum READ endpoint can rebuild the same
        # portfolio-alignment reasoning it shows on create (the deficit facts are
        # otherwise recomputed only at compute time). Additive to the request_input
        # JSONB audit blob — floats are already JSON-safe.
        if deficit_facts is not None:
            _extras["deficit_facts"] = deficit_facts
    if focus_category:
        _extras["focus_category"] = focus_category
    if rebal_run_id is not None:
        # str(), not the raw UUID: request_extras merges into the request_input
        # JSONB and json.dumps cannot serialise UUID (audit F4 — the best-effort
        # persist except would swallow the failure silently).
        _extras["sip_rebal_run_id"] = str(rebal_run_id)
    if _extras:
        request_extras = _extras

    run_id: Optional[uuid.UUID] = None
    if persist:
        if progress:
            await progress(92, "Saving your plan…")
        # ``chat_session_id`` is None for a non-chat create (the Invest-page
        # "Start a SIP" endpoint): the run still persists, just unlinked to a
        # chat session (the FK column is nullable). In the chat path it is always
        # set, so this is unchanged there.
        # Best-effort (mirrors paa_engine/chat.py): the BUY list is already
        # computed, so a persistence failure is logged loudly (surfaces in alerts)
        # but never denies the user the recommendation. Flush only — the caller
        # (chat router / create service) owns the commit.
        try:
            if saved_investment_preference_id is DERIVE_PREFERENCE_ID:
                saved_pref_id = preference_id_for(
                    user,
                    applied=paa_outcome.result.human_override_applied is not None,
                )
            else:
                saved_pref_id = saved_investment_preference_id
            source_allocation_run_id = await persist_practical_allocation_run(
                db,
                user_id=acting_user_id,
                # The REAL-corpus allocation on purpose, not effective_result: the
                # sized one is a ₹1cr notional used only to recover ratios.
                output=paa_outcome.result,
                chat_session_id=chat_session_id,
                user_question=user_question,
                saved_investment_preference_id=saved_pref_id,
            )
            run_id = await persist_additional_investment_recommendation(
                db,
                acting_user_id,
                response,
                source_allocation_run_id=source_allocation_run_id,
                chat_session_id=chat_session_id,
                user_question=user_question,
                request=inp,
                request_extras=request_extras,
                saved_investment_preference_id=saved_pref_id,
                origin=origin,
            )

            if cadence is Cadence.SIP_MONTHLY:
                # A monthly SIP set here IS the customer's monthly SIP, wherever
                # they set it (chat or the Invest page). Write the canonical
                # `starting_monthly_investment` so the goal planner, goals timeline
                # and IPS all move with it, and invalidate the cached goal-plan run
                # since a SIP is a cashflow input. Both enlist in the caller's
                # transaction, so the plan and the amount land together or not at
                # all. Lumpsum is a one-off deployment, not a SIP — never synced.
                await set_starting_monthly_investment(
                    db, acting_user_id, deploy_amount_inr
                )
                await mark_cashflow_stale(db, acting_user_id, commit=False)
        except Exception:  # noqa: BLE001 — best-effort persist, never blocks the reply
            logger.exception(
                "Failed to persist additional_investment run for session=%s — "
                "returning recommendation anyway; investigate",
                chat_session_id,
            )
            run_id = None

    return AdditionalInvestmentRunOutcome(
        output=response,
        run_id=run_id,
        deficit_facts=deficit_facts,
        practical_result=effective_result,
    )
