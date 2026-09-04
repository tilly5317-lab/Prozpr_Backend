"""Preference save orchestration (spec §4.4). Preview is side-effect-free;
confirm is one transaction; the anti-ratchet check runs on customer_choices
BEFORE any resolution.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.observability import capture_preference_cleared, capture_preference_saved
from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.mutual_funds.services.investment_preferences import (
    ResolvedPreferences,
    resolve_saved_preferences,
)
from app.domains.profile.models.saved_investment_preference import (
    SavedInvestmentPreference,
)
from app.domains.practical_asset_allocation.services.paa_engine.input_builder import (
    build_practical_allocation_input_for_user,
)
from app.domains.practical_asset_allocation.services.paa_engine.service import (
    compute_practical_allocation_result,
)

ensure_ai_agents_path()

from practical_asset_allocation.pipeline import (  # type: ignore[import-not-found]  # noqa: E402
    run_practical_allocation,
)

logger = logging.getLogger(__name__)

# No-run-yet fallbacks (spec §4.4 One-run rule). Beta shares are % of the
# equity CLASS, mirroring the engine's DEFAULT_CLASS_COMPOSITION constants.
_FALLBACK_CLASS_MIX = {"equity": 60.0, "debt": 35.0, "others": 5.0}
# Mirrors the engine's DEFAULT_CLASS_COMPOSITION constants (share of own class).
_FALLBACK_SUBGROUP_SHARES = {
    "low_beta_equities": 40.0,
    "medium_beta_equities": 40.0,
    "high_beta_equities": 20.0,
    "short_debt": 60.0,
    "arbitrage": 40.0,
    "gold_commodities": 100.0,
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
    one (today: gold_commodities → others). multi_asset excluded (it is a
    fixed contributor, not a settable lever)."""
    from practical_asset_allocation.human_override import (
        CLASS_OF, FROZEN_SUBGROUPS, SETTABLE_SUBGROUPS,
    )

    by_class: dict[str, list[str]] = {}
    for sg in SETTABLE_SUBGROUPS:
        if sg in FROZEN_SUBGROUPS or sg == "multi_asset":
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
    → one no-persist practical run, each row's share of its own migratable
    class (the same basis ``_apply_emphasis`` uses). Fallbacks: class 60/35/5,
    subgroup shares from the engine's default class composition — used both
    when the user has no run yet AND when the latest run's mix is degenerate
    (sums to <= 0), so a corrupt/empty row can never silently resolve "more"
    to LESS.
    """
    from app.domains.practical_asset_allocation.models.run import (
        PracticalAssetAllocationRun,
    )
    from practical_asset_allocation.human_override import CLASS_OF, FROZEN_SUBGROUPS

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
            # Base EXCLUDES multi_asset: it is a fixed contributor, not an
            # adjustable peer (ruling 10 + finding #2), so a subgroup's
            # "current share" must be a share of the migratable sleeve — the
            # same basis _apply_emphasis targets.
            class_base = {
                cls: sum(
                    r.total
                    for r in result.aggregated_subgroups
                    if CLASS_OF.get(r.subgroup, "others") == cls
                    and r.subgroup not in FROZEN_SUBGROUPS
                    and r.subgroup != "multi_asset"
                )
                for cls in ("equity", "debt", "others")
            }
            # A run exists → the computed shares are the WHOLE truth. A
            # subgroup with no row genuinely holds 0% of its class; the
            # resolver's .get(sg, 0.0) supplies that. The fallback must NOT
            # act as a per-subgroup floor here (it would resolve "more X" for
            # a customer holding no X off a fabricated baseline — moving real
            # money into a category they don't hold). Fallback is the
            # no-run-yet cold start only.
            shares = {
                r.subgroup: r.total
                * 100.0
                / class_base[CLASS_OF.get(r.subgroup, "others")]
                for r in result.aggregated_subgroups
                if r.subgroup not in FROZEN_SUBGROUPS
                and r.subgroup != "multi_asset"
                and class_base[CLASS_OF.get(r.subgroup, "others")] > 0
            }

    return class_mix, shares


async def _run_preferred(user, prefs):
    """One practical run with the resolved preference as a one-off (no persist).

    Returns ``(result, blocking_message)`` — ``result`` is None exactly when
    the practical engine was blocked (e.g. zero corpus), in which case
    ``blocking_message`` carries the customer-facing reason."""
    one_off = {
        "asset_class_requested": prefs.asset_class_requested,
        "subgroup_emphasis": prefs.subgroup_emphasis,
    }
    outcome = await compute_practical_allocation_result(
        user, "preferences preview", chat_ctx=_build_ctx(user, one_off=one_off)
    )
    return outcome.result, outcome.blocking_message


def _preferred_view(preferred) -> tuple[Optional[dict], Optional[str]]:
    """(achieved, shortfall_reason) off a preferred practical output — tolerant
    of a None output (engine failure)."""
    applied = getattr(preferred, "human_override_applied", None) if preferred else None
    if applied is None:
        return None, None
    return getattr(applied, "achieved", None), getattr(applied, "shortfall_reason", None)


async def _persist_confirm(db, user, resolved, intent, preferred_out, prior_row):
    from app.domains.asset_allocation.models.run import (
        AssetAllocationRun,
        AssetAllocationRunStatus,
    )
    from app.domains.asset_allocation.services.aa_engine.service import (
        compute_allocation_result,
    )

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

    requested = resolved.asset_class_requested or {}
    target = achieved or {}
    row = SavedInvestmentPreference(
        user_id=user.id,
        equity_requested_pct=requested.get("equity"),
        debt_requested_pct=requested.get("debt"),
        others_requested_pct=requested.get("others"),
        equity_target_pct=target.get("equity"),
        debt_target_pct=target.get("debt"),
        others_target_pct=target.get("others"),
        resolved_targets=resolved.subgroup_emphasis or None,
        customer_choices=intent,
    )
    db.add(row)

    # Without this refresh, the ideal-parity read inside compute_allocation_result
    # (via load_human_override_for_user(user)) sees a stale relationship and
    # persists an un-bent allocation.
    await db.flush()
    await db.refresh(user, ["saved_investment_preference"])

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

    await db.commit()


async def _persist_neutral_after_clear(db, user):
    """Same compute_allocation_result call — the user now has no row, so it
    persists a neutral (un-bent) allocation."""
    from app.domains.asset_allocation.services.aa_engine.service import (
        compute_allocation_result,
    )

    await compute_allocation_result(
        user,
        "preference save",
        db=db,
        persist_recommendation=True,
        acting_user_id=user.id,
        chat_ctx=_build_ctx(user),
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

    try:
        from app.domains.additional_investment.models.additional_investment_run import (
            AdditionalInvestmentRun,
            Cadence,
        )

        stmt = (
            select(AdditionalInvestmentRun)
            .where(
                AdditionalInvestmentRun.user_id == user_id,
                AdditionalInvestmentRun.cadence == Cadence.SIP_MONTHLY,
            )
            .order_by(AdditionalInvestmentRun.created_at.desc())
            .limit(1)
        )
        latest_sip = (await db.execute(stmt)).scalars().first()
        if latest_sip is not None:
            from app.domains.additional_investment.services.ainv_engine.service import (
                compute_additional_investment_result,
            )

            await compute_additional_investment_result(
                user,
                "preference refresh",
                db=db,
                acting_user_id=user_id,
                chat_session_id=None,
                deploy_amount_inr=float(latest_sip.deploy_amount_inr),
                cadence=Cadence.SIP_MONTHLY,
                chat_ctx=_build_ctx(user),
                persist=True,
            )
            await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(
            "eager refresh: SIP recompute failed for user_id=%s", user_id
        )


async def recommendation_block(user, row) -> Optional[dict]:
    """The neutral (no-preference) recommended mix, for the GET screen.

    None when the user has no saved row — the persisted current allocation
    IS the neutral in that case, and the screen already reads it elsewhere.
    """
    if row is None:
        return None

    inp, _ = build_practical_allocation_input_for_user(
        _build_ctx(user), apply_saved_preferences=False
    )
    out = await asyncio.to_thread(run_practical_allocation, inp)
    rec = out.asset_class_breakdown.recommended
    return {
        "equity": rec.equity_total_pct,
        "debt": rec.debt_total_pct,
        "others": rec.others_total_pct,
    }


async def preview_or_save(db, user, intent: dict, *, confirm: bool):
    from app.domains.profile.schemas import InvestmentPreferencePreviewResponse

    # Normalize sole-class subgroup asks (e.g. "more gold" → "more others")
    # BEFORE canonicalising, change-detection, storage and resolution — so
    # the whole facet machinery, idempotence, and the stored customer_choices
    # all see one consistent form.
    intent = _route_sole_class_subgroups(intent or {})
    intent = _canonical_intent(intent)
    row = await active_preference_row(db, user.id)
    stored_intent = _canonical_intent(getattr(row, "customer_choices", None) or {})

    if intent == stored_intent:
        return InvestmentPreferencePreviewResponse(no_op=True)

    if not intent:  # clear
        if confirm and row is not None:
            # Soft clear: rows are immutable history (runs FK them) — flip
            # is_active, never delete. Flush before any further engine call —
            # a pending write left for autoflush can get swept up inside a
            # lazy-load SELECT fired deep in the input builder, and a failure
            # there mid-flush leaves the session's transaction unusable for
            # the commit below (SQLAlchemy marks it DEACTIVE on a failed
            # autoflush rollback).
            row.is_active = False
            await db.flush()
            await db.refresh(user, ["saved_investment_preference"])
            await _persist_neutral_after_clear(db, user)
            await db.commit()
            user_id = user.id  # captured before _eager_refresh: a mid-refresh
            # rollback expires `user` (see _eager_refresh's own docstring).
            await _eager_refresh(db, user)
            capture_preference_cleared(distinct_id=user_id)
        return InvestmentPreferencePreviewResponse(no_op=row is None)

    changed = _changed_intent(intent, stored_intent)
    need_shares = any(
        token in _RELATIVE_TOKENS
        for token in (changed.get("subgroups") or {}).values()
    )
    class_mix, subgroup_shares = await _current_mixes(
        db, user, need_subgroup_shares=need_shares
    )
    resolved_changed = resolve_saved_preferences(
        changed,
        current_class_mix_pct=class_mix,
        current_subgroup_share_pct=subgroup_shares,
    )
    resolved = _merge_field_level(intent, row, resolved_changed, changed)

    preferred, blocking_message = await _run_preferred(user, resolved)
    if preferred is None:
        # Never write a half-save — a blocked compute (e.g. zero corpus)
        # must leave no row, no run, and no refresh, or an identical retry
        # would find the stored intent unchanged and permanently no-op.
        return InvestmentPreferencePreviewResponse(
            shortfall=blocking_message or _DEFAULT_BLOCKED_SHORTFALL,
            no_op=False,
        )

    achieved, shortfall = _preferred_view(preferred)

    recommended_block = getattr(
        getattr(preferred, "asset_class_breakdown", None), "recommended", None
    )
    recommendation = (
        {
            "equity": recommended_block.equity_total_pct,
            "debt": recommended_block.debt_total_pct,
            "others": recommended_block.others_total_pct,
        }
        if recommended_block is not None
        else class_mix
    )
    deviation = (
        {k: round(achieved.get(k, 0.0) - class_mix.get(k, 0.0), 1) for k in achieved}
        if achieved
        else None
    )

    if confirm:
        await _persist_confirm(db, user, resolved, intent, preferred, row)
        user_id = user.id  # captured before _eager_refresh: a mid-refresh
        # rollback expires `user` (see _eager_refresh's own docstring).
        await _eager_refresh(db, user)
        fields_set = [
            field
            for field in ("asset_class_requested", "subgroup_emphasis")
            if getattr(resolved, field, None) not in (None, [], {})
        ]
        capture_preference_saved(
            fields_set=fields_set,
            applied_defaults=resolved.applied_defaults or {},
            shortfall=shortfall is not None,
            distinct_id=user_id,
        )

    return InvestmentPreferencePreviewResponse(
        recommendation=recommendation,
        preferred=achieved,
        deviation=deviation,
        shortfall=shortfall,
    )
