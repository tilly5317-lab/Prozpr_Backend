"""Preference save orchestration (spec §4.4). Preview is side-effect-free;
confirm is one transaction; the anti-ratchet check runs on customer_choices
BEFORE any resolution.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.observability import capture_preference_saved
from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.mutual_funds.services.investment_preferences import (
    ResolvedPreferences,
    resolve_saved_preferences,
)
from app.domains.profile.models.saved_investment_preference import (
    SavedInvestmentPreference,
)
from app.domains.practical_asset_allocation.services.paa_engine.service import (
    compute_practical_allocation_result,
)

ensure_ai_agents_path()

logger = logging.getLogger(__name__)

# No-run-yet fallbacks (spec §4.4 One-run rule). Beta shares are % of the
# equity CLASS.
_FALLBACK_CLASS_MIX = {"equity": 60.0, "debt": 35.0, "others": 5.0}
# The reviewed neutral composition, as a share of the WHOLE portfolio (D-A3)
# — the per-class composition (40/40/20, 60/40, 100) carried through
# _FALLBACK_CLASS_MIX, so the six add to 100.
_FALLBACK_SUBGROUP_SHARES = {
    "low_beta_equities": 24.0,     # 40% of the 60% equity class
    "medium_beta_equities": 24.0,  # 40% of equity
    "high_beta_equities": 12.0,    # 20% of equity
    "short_debt": 21.0,            # 60% of the 35% debt class
    "arbitrage": 14.0,             # 40% of debt
    "gold_commodities": 5.0,       # 100% of the 5% commodity class
}

# Honest fallback when a blocked practical run carried no message of its own.
_DEFAULT_BLOCKED_SHORTFALL = (
    "We couldn't compute a preview right now — please try again in a moment."
)

# Relative tokens need the subgroup's current class share to resolve; a
# bare number or "none" does not.
_RELATIVE_TOKENS = ("more", "heavy", "less")


def _sole_settable_subgroup_by_class() -> dict:
    """{subgroup: class} for each class whose ONLY settable subgroup is that
    one (today: gold_commodities → others). Only the frozen HOLDINGS rows are
    left out — they are not levers the customer can pull."""
    from practical_asset_allocation.human_override import (
        CLASS_OF, FROZEN_SUBGROUPS, SETTABLE_SUBGROUPS,
    )

    by_class: dict[str, list[str]] = {}
    for sg in SETTABLE_SUBGROUPS:
        if sg in FROZEN_SUBGROUPS:
            continue
        by_class.setdefault(CLASS_OF.get(sg, "others"), []).append(sg)
    return {subs[0]: cls for cls, subs in by_class.items() if len(subs) == 1}


_SOLE_CLASS_SUBGROUP = _sole_settable_subgroup_by_class()


def _route_sole_class_subgroups(intent: dict) -> dict:
    """A subgroup that is the ONLY settable subgroup of its class (gold in
    'others') is really a CLASS control: a within-class emphasis cannot grow
    a one-category class, so "more gold" would silently no-op. Lift such a
    token into the asset_class facet so it moves the class allocation — but
    only when the customer hasn't also set an explicit asset_class facet
    (which the single normalize_tilt target can't compose with)."""
    if "asset_class" in intent:
        return intent
    subs = dict(intent.get("subgroups") or {})
    for sg, cls in _SOLE_CLASS_SUBGROUP.items():
        if sg not in subs:
            continue
        token = subs[sg]
        if isinstance(token, (int, float)) and not isinstance(token, bool):
            routed = {"class": cls, "direction": "target", "target_pct": float(token)}
        elif token in ("more", "heavy", "less", "none"):
            routed = {"class": cls, "direction": token}  # same 5-state vocabulary
        else:
            continue
        subs.pop(sg)
        out = dict(intent)
        out["asset_class"] = routed
        if subs:
            out["subgroups"] = subs
        else:
            out.pop("subgroups", None)
        return out
    return intent


def _canonical_intent(intent: dict) -> dict:
    return {k: v for k, v in (intent or {}).items() if v not in (None, [], {})}


def _follow_stored_arbitrage_key(intent: dict, stored_intent: dict) -> dict:
    """The screen always says "arbitrage"; storage may hold the held twin.
    Re-key the ask to whichever twin the stored intent already uses so an
    unchanged re-send is a no-op (anti-ratchet)."""
    subs = intent.get("subgroups") or {}
    stored_subs = stored_intent.get("subgroups") or {}
    for mine, theirs in (("arbitrage", "arbitrage_plus_income"), ("arbitrage_plus_income", "arbitrage")):
        if mine in subs and theirs in stored_subs and mine not in stored_subs:
            new_subs = {**subs}
            new_subs[theirs] = new_subs.pop(mine)
            return {**intent, "subgroups": new_subs}
    return intent


async def active_preference_row(db: AsyncSession, user_id) -> Optional[SavedInvestmentPreference]:
    stmt = select(SavedInvestmentPreference).where(
        SavedInvestmentPreference.user_id == user_id,
        SavedInvestmentPreference.is_active.is_(True),
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def _build_ctx(user, one_off: dict | None = None):
    """Router-built ``TurnContext`` for an out-of-chat engine call.

    Required fields fill with neutral values; ``one_off`` (a resolved-
    preferences dict) only lands in ``chat_overrides`` when given, so the
    one-off path exercises the same precedence machinery production uses.
    """
    from app.domains.ai_engine.turn_context import TurnContext

    return TurnContext(
        user_ctx=user,
        user_question="investment preferences",
        conversation_history=[],
        client_context=None,
        session_id=uuid.uuid4(),
        db=None,
        effective_user_id=getattr(user, "id", None) or uuid.uuid4(),
        last_agent_runs={},
        active_intent=None,
        chat_overrides={"human_override_preferences": one_off}
        if one_off is not None
        else None,
    )


def _changed_intent(intent: dict, stored_intent: dict) -> dict:
    """The sub-intent that must be resolved fresh: the asset_class facet if
    it changed, plus ONLY the subgroup entries whose token changed. Anything
    unchanged reuses its stored resolved value (idempotence — re-resolving a
    relative token against an already-preferred baseline would ratchet)."""
    changed: dict = {}
    if "asset_class" in intent and intent["asset_class"] != stored_intent.get(
        "asset_class"
    ):
        changed["asset_class"] = intent["asset_class"]
    stored_sub = stored_intent.get("subgroups") or {}
    changed_sub = {
        sg: token
        for sg, token in (intent.get("subgroups") or {}).items()
        if stored_sub.get(sg) != token
    }
    if changed_sub:
        changed["subgroups"] = changed_sub
    return changed


def _merge_field_level(
    intent: dict, row, resolved_changed: ResolvedPreferences, changed: dict
) -> ResolvedPreferences:
    """Fresh resolution for changed entries; stored values for the rest. A
    facet/entry the new intent omits entirely is dropped."""
    if "asset_class" in changed:
        asset_class = resolved_changed.asset_class_requested
    elif "asset_class" in intent and row is not None:
        asset_class = row.asset_class_requested
    else:
        asset_class = None

    stored_emphasis = (row.resolved_targets or {}) if row is not None else {}
    changed_sub = changed.get("subgroups") or {}
    emphasis: dict[str, float] = {}
    for sg in intent.get("subgroups") or {}:
        if sg in changed_sub:
            emphasis[sg] = resolved_changed.subgroup_emphasis[sg]
        elif sg in stored_emphasis:
            emphasis[sg] = stored_emphasis[sg]

    return ResolvedPreferences(
        asset_class_requested=asset_class,
        subgroup_emphasis=emphasis,
        applied_defaults=dict(resolved_changed.applied_defaults),
    )


async def _resolve_against_row(
    db, user, intent: dict, row
) -> tuple[ResolvedPreferences, dict, dict, dict]:
    """Returns ``(resolved, changed, current_class_mix, intent)``; only facets
    that differ from ``row`` re-resolve. The returned ``intent`` may have
    ``subgroups["arbitrage"]`` re-keyed to ``arbitrage_plus_income`` when the
    customer's current run holds that subgroup and not ``arbitrage``."""
    stored_intent = _canonical_intent(getattr(row, "customer_choices", None) or {})
    changed = _changed_intent(intent, stored_intent)
    changed_subs = changed.get("subgroups") or {}
    need_shares = any(token in _RELATIVE_TOKENS for token in changed_subs.values())
    need_shares = need_shares or "arbitrage" in changed_subs
    class_mix, subgroup_shares = await _current_mixes(
        db, user, need_subgroup_shares=need_shares
    )
    if "arbitrage" in changed_subs and subgroup_shares.get(
        "arbitrage_plus_income", 0.0
    ) > subgroup_shares.get("arbitrage", 0.0):
        # One customer word, two engine subgroups: the practical pipeline funds medium-term debt through arbitrage_plus_income.
        new_changed_subs = dict(changed_subs)
        new_changed_subs["arbitrage_plus_income"] = new_changed_subs.pop("arbitrage")
        changed = {**changed, "subgroups": new_changed_subs}
        intent_subs = dict(intent.get("subgroups") or {})
        intent_subs["arbitrage_plus_income"] = intent_subs.pop("arbitrage")
        intent = {**intent, "subgroups": intent_subs}
    resolved_changed = resolve_saved_preferences(
        changed,
        current_class_mix_pct=class_mix,
        current_subgroup_share_pct=subgroup_shares,
    )
    return (
        _merge_field_level(intent, row, resolved_changed, changed),
        changed,
        class_mix,
        intent,
    )


async def _current_mixes(db, user, *, need_subgroup_shares: bool):
    """Current working class mix + beta-subgroup class shares — the numbers
    the customer sees.

    ONE-RUN RULE: the class mix is READ, never recomputed — the latest
    ``practical_asset_allocation_runs`` row stores equity/debt/others_total_pct
    directly (SELECT ... ORDER BY created_at DESC LIMIT 1; no status/spine
    filter — latest row wins). This is the practical (holdings-aware) run the
    customer actually sees, per spec §4.2 — NOT ``asset_allocation_runs``,
    whose equity/debt/others_total_pct columns are always zero (a separate,
    pre-existing persistence bug in write_asset_allocation_run.py). Only a
    relative subgroup token (more/heavy/less) forces one engine run
    (per-subgroup values aren't on the run row): ``need_subgroup_shares=True``
    → one no-persist practical run, each row's share of the WHOLE portfolio
    (the basis the engine honours sub-group asks in — D-A3). Fallbacks: class 60/35/5,
    subgroup shares from the engine's default class composition — used both
    when the user has no run yet AND when the latest run's mix is degenerate
    (sums to <= 0), so a corrupt/empty row can never silently resolve "more"
    to LESS.
    """
    from app.domains.practical_asset_allocation.models.run import (
        PracticalAssetAllocationRun,
    )
    from practical_asset_allocation.human_override import FROZEN_SUBGROUPS

    stmt = (
        select(PracticalAssetAllocationRun)
        .where(PracticalAssetAllocationRun.user_id == user.id)
        .order_by(PracticalAssetAllocationRun.created_at.desc())
        .limit(1)
    )
    latest = (await db.execute(stmt)).scalars().first()

    class_mix = dict(_FALLBACK_CLASS_MIX)
    if latest is not None:
        candidate = {
            "equity": float(latest.equity_total_pct),
            "debt": float(latest.debt_total_pct),
            "others": float(latest.others_total_pct),
        }
        if sum(candidate.values()) > 0:
            class_mix = candidate

    shares = dict(_FALLBACK_SUBGROUP_SHARES)
    if need_subgroup_shares:
        outcome = await compute_practical_allocation_result(
            user, "preferences preview", chat_ctx=_build_ctx(user)
        )
        result = outcome.result
        if result is not None:
            # Share of the WHOLE portfolio (D-A3) — the one basis storage, the
            # screen catalog and the engine all speak, so "more gold" adds ten
            # points of the portfolio and the stored number means the same
            # thing wherever it is read. multi_asset is in: the sleeve is an
            # adjustable peer now (D-A2). Only the frozen HOLDINGS rows stay
            # out; the customer cannot move those.
            grand = float(getattr(result, "grand_total", 0.0))
            # A run exists → the computed shares are the WHOLE truth. A
            # subgroup with no row genuinely holds 0% of the portfolio; the
            # resolver's .get(sg, 0.0) supplies that. The fallback must NOT
            # act as a per-subgroup floor here (it would resolve "more X" for
            # a customer holding no X off a fabricated baseline — moving real
            # money into a category they don't hold). Fallback is the
            # no-run-yet cold start only.
            if grand > 0:
                shares = {
                    r.subgroup: r.total * 100.0 / grand
                    for r in result.aggregated_subgroups
                    if r.subgroup not in FROZEN_SUBGROUPS
                }

    return class_mix, shares


async def _run_preferred(user, prefs):
    """One practical run with the resolved preference as a one-off (no persist).

    Returns ``(result, blocking_message)`` — ``result`` is None exactly when
    the practical engine was blocked (e.g. zero corpus), in which case
    ``blocking_message`` carries the customer-facing reason."""
    one_off = one_off_override(prefs)
    outcome = await compute_practical_allocation_result(
        user, "preferences preview", chat_ctx=_build_ctx(user, one_off=one_off)
    )
    return outcome.result, outcome.blocking_message


def achieved_class_mix(practical_output) -> Optional[dict]:
    """The class mix the *_target_pct columns record — "what was achievable at
    save time" — read off the run's FINAL breakdown. The engine stopped
    carrying it on ``human_override_applied`` (spec §6); the breakdown was
    always where that number came from. None when the run consumed no
    preference, so a neutral run leaves the columns empty as before.
    Tolerant of a None output (engine failure)."""
    if practical_output is None:
        return None
    if getattr(practical_output, "human_override_applied", None) is None:
        return None
    block = getattr(
        getattr(practical_output, "asset_class_breakdown", None), "recommended", None
    )
    if block is None:
        return None
    return {
        "equity": block.equity_total_pct,
        "debt": block.debt_total_pct,
        "others": block.others_total_pct,
    }


def _preferred_view(preferred) -> tuple[Optional[dict], Optional[str]]:
    """(achieved, shortfall_reason) off a preferred practical output — tolerant
    of a None output (engine failure)."""
    applied = getattr(preferred, "human_override_applied", None) if preferred else None
    if applied is None:
        return None, None
    return achieved_class_mix(preferred), getattr(applied, "shortfall_reason", None)


def _new_row(user_id, intent, resolved, achieved, *, active: bool, supersedes_id=None):
    requested = resolved.asset_class_requested or {}
    target = achieved or {}
    return SavedInvestmentPreference(
        supersedes_id=supersedes_id,
        user_id=user_id,
        equity_requested_pct=requested.get("equity"),
        debt_requested_pct=requested.get("debt"),
        others_requested_pct=requested.get("others"),
        equity_target_pct=target.get("equity"),
        debt_target_pct=target.get("debt"),
        others_target_pct=target.get("others"),
        resolved_targets=resolved.subgroup_emphasis or None,
        customer_choices=intent,
        applied_defaults=getattr(resolved, "applied_defaults", None) or None,
        is_active=active,
        activated_at=datetime.now(timezone.utc) if active else None,
    )


async def _persist_preferred_allocation(db, user):
    from app.domains.asset_allocation.models.run import (
        AssetAllocationRun,
        AssetAllocationRunStatus,
    )
    from app.domains.asset_allocation.services.aa_engine.service import (
        compute_allocation_result,
    )

    prior_stmt = (
        select(AssetAllocationRun.id)
        .where(AssetAllocationRun.user_id == user.id)
        .order_by(AssetAllocationRun.created_at.desc())
        .limit(1)
    )
    prior_id = (await db.execute(prior_stmt)).scalars().first()

    outcome = await compute_allocation_result(
        user,
        "preference save",
        db=db,
        persist_recommendation=True,
        acting_user_id=user.id,
        chat_ctx=_build_ctx(user),
    )

    if outcome.asset_allocation_run_id is not None:
        new_run = await db.get(AssetAllocationRun, outcome.asset_allocation_run_id)
        if new_run is not None:
            new_run.status = AssetAllocationRunStatus.approved
            new_run.supersedes_id = prior_id


async def _persist_confirm(db, user, resolved, intent, preferred_out, prior_row):
    achieved, _ = _preferred_view(preferred_out)

    # Immutable versioned rows: deactivate the prior row, insert a fresh one.
    # Runs keep their FK to the old row — history stays truthful; "current"
    # is the single is_active row.
    if prior_row is not None:
        prior_row.is_active = False
        # Flush the deactivation before inserting: the partial unique index
        # allows only ONE active row per user, and without ordering the
        # INSERT can hit the index before the UPDATE lands.
        await db.flush()

    row = _new_row(
        user.id, intent, resolved, achieved, active=True,
        supersedes_id=prior_row.id if prior_row is not None else None,
    )
    db.add(row)

    # Without this refresh, the ideal-parity read inside compute_allocation_result
    # (via load_human_override_for_user(user)) sees a stale relationship and
    # persists an un-bent allocation.
    await db.flush()
    await db.refresh(user, ["saved_investment_preference"])

    await _persist_preferred_allocation(db, user)
    await db.commit()


async def _refresh_standing_plan(db, user, user_id, cadence, label) -> None:
    """Recompute + persist the customer's latest additional-investment plan of
    one cadence (SIP or lump sum), if one exists, against the new preference —
    reusing its stored deploy amount. Best-effort and self-committing like the
    rebalancing block below: a failure rolls back and self-heals on next read."""
    try:
        from app.domains.additional_investment.models.additional_investment_run import (
            AdditionalInvestmentRun,
        )

        stmt = (
            select(AdditionalInvestmentRun)
            .where(
                AdditionalInvestmentRun.user_id == user_id,
                AdditionalInvestmentRun.cadence == cadence,
            )
            .order_by(AdditionalInvestmentRun.created_at.desc())
            .limit(1)
        )
        latest = (await db.execute(stmt)).scalars().first()
        if latest is not None:
            from app.domains.additional_investment.services.ainv_engine.service import (
                compute_additional_investment_result,
            )

            await compute_additional_investment_result(
                user,
                "preference refresh",
                db=db,
                acting_user_id=user_id,
                chat_session_id=None,
                deploy_amount_inr=float(latest.deploy_amount_inr),
                cadence=cadence,
                chat_ctx=_build_ctx(user),
                persist=True,
            )
            await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(
            "eager refresh: %s recompute failed for user_id=%s", label, user_id
        )


async def _eager_refresh(db, user):
    """Refresh the customer-visible plans that read the saved preference.

    Each block is independently wrapped AND independently committed — the
    compute helpers flush only (caller owns the transaction), so skipping the
    per-block commit would let teardown roll the refreshed plan back. A
    failed block rolls back FIRST (a mid-flush failure otherwise leaves the
    transaction DEACTIVE and the sibling block dies on PendingRollbackError),
    then self-heals via the freshness check on the next read. ``user_id`` is
    captured before any rollback: a rollback expires ``user``, and touching
    it afterwards raises MissingGreenlet. A committed saved plan
    (origin='saved') is deliberately never touched — it stays what the
    customer picked until they re-save.
    """
    from app.domains.rebalancing.services.rebal_engine.service import (
        compute_rebalancing_result,
    )

    user_id = user.id

    try:
        await compute_rebalancing_result(
            user,
            "preference refresh",
            db=db,
            acting_user_id=user_id,
            chat_session_id=None,
            persist=True,
            origin=None,
            chat_ctx=_build_ctx(user),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(
            "eager refresh: rebalancing recompute failed for user_id=%s", user_id
        )

    from app.domains.additional_investment.models.additional_investment_run import Cadence

    # SIP and lump sum: recompute each standing plan (if the customer has one)
    # against the new preference, so its tab reflects the save like rebalancing.
    await _refresh_standing_plan(db, user, user_id, Cadence.SIP_MONTHLY, "SIP")
    await _refresh_standing_plan(db, user, user_id, Cadence.LUMPSUM, "lump-sum")


async def resolve_one_off(db, user, chat_intent: dict, *, base_row=None):
    """``base_row`` overrides the active saved row as the merge base. A class
    facet in the ask replaces the base's — one class at a time; subgroups merge
    per key. Returns ``(merged_intent, resolved, changed)``."""
    row = base_row if base_row is not None else await active_preference_row(db, user.id)
    stored = _canonical_intent(getattr(row, "customer_choices", None) or {})
    ask = _canonical_intent(_route_sole_class_subgroups(chat_intent or {}))
    ask = _follow_stored_arbitrage_key(ask, stored)
    intent: dict = {}
    asset_class = ask.get("asset_class", stored.get("asset_class"))
    if asset_class:
        intent["asset_class"] = asset_class
    subgroups = {**(stored.get("subgroups") or {}), **(ask.get("subgroups") or {})}
    if subgroups:
        intent["subgroups"] = subgroups
    resolved, changed, _, intent = await _resolve_against_row(db, user, intent, row)
    return intent, resolved, changed


def one_off_override(resolved: ResolvedPreferences) -> dict:
    return {
        "asset_class_requested": resolved.asset_class_requested,
        "subgroup_emphasis": resolved.subgroup_emphasis,
    }


def fill_candidate_targets(
    candidate, achieved: dict | None, shortfall_reason: str | None = None
) -> None:
    """Set a candidate row's target columns after the run that produced them
    (AINV inserts the row before its engine run, so targets arrive later)."""
    if achieved is None:
        return
    candidate.equity_target_pct = achieved.get("equity")
    candidate.debt_target_pct = achieved.get("debt")
    candidate.others_target_pct = achieved.get("others")
    if shortfall_reason is not None:
        candidate.shortfall_reason = shortfall_reason


async def insert_candidate(db, user, intent, resolved, achieved, shortfall_reason=None):
    """A seen-but-unsaved what-if as a real inactive row, so the run can FK it."""
    row = _new_row(user.id, intent, resolved, achieved, active=False)
    row.shortfall_reason = shortfall_reason
    db.add(row)
    await db.flush()
    return row


async def candidate_row(db, user_id, candidate_id):
    """The user's preference row by id, in any state — the caller reads
    ``is_active`` / ``activated_at`` to tell a live candidate from one that
    was already saved or superseded."""
    stmt = select(SavedInvestmentPreference).where(
        SavedInvestmentPreference.id == candidate_id,
        SavedInvestmentPreference.user_id == user_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def activate_candidate_for_run(db, user, preference_id) -> bool:
    """'Save plan' saves the preference that shaped it: activate the run's
    candidate row iff it is still a live candidate."""
    if preference_id is None:
        return False
    row = await candidate_row(db, user.id, preference_id)
    if row is None or row.is_active or row.activated_at is not None:
        return False
    result = await confirm_candidate(db, user, row)
    return not result.no_op


async def confirm_candidate(db, user, candidate):
    """'Yes, save it': activate the candidate the customer already saw. No
    re-resolution — the numbers they consented to are the row's."""
    from app.domains.profile.schemas import InvestmentPreferencePreviewResponse

    prior = await active_preference_row(db, user.id)
    stored_intent = _canonical_intent(getattr(prior, "customer_choices", None) or {})
    if candidate.is_active or _canonical_intent(candidate.customer_choices or {}) == stored_intent:
        return InvestmentPreferencePreviewResponse(no_op=True)

    if prior is not None:
        prior.is_active = False
        await db.flush()  # partial unique index: deactivate before activating
        candidate.supersedes_id = prior.id
    candidate.is_active = True
    candidate.activated_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(user, ["saved_investment_preference"])
    await _persist_preferred_allocation(db, user)
    await db.commit()

    user_id = user.id
    requested, target = candidate.asset_class_requested, candidate.asset_class_target
    resolved_targets = candidate.resolved_targets  # captured before _eager_refresh:
    # a mid-refresh rollback expires `candidate` (see _eager_refresh's own docstring).
    stored_applied_defaults = getattr(candidate, "applied_defaults", None)
    stored_shortfall_reason = getattr(candidate, "shortfall_reason", None)
    await _eager_refresh(db, user)
    fields_set = [
        name for name, value in (
            ("asset_class_requested", requested),
            ("subgroup_emphasis", resolved_targets),
        ) if value
    ]
    capture_preference_saved(
        fields_set=fields_set,
        applied_defaults=stored_applied_defaults or {},
        shortfall=stored_shortfall_reason is not None,
        distinct_id=user_id,
    )
    return InvestmentPreferencePreviewResponse(
        preferred=target, no_op=False,
    )
