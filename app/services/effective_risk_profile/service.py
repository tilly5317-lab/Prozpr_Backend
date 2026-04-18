"""Effective risk profile — `service.py`.

App-layer persistence and calculation helpers for the user’s effective risk assessment (distinct from the deterministic ``risk_profiling.scoring`` used when building ``AllocationInput`` for ideal allocation).
"""


from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.profile import InvestmentProfile, PersonalFinanceProfile, RiskProfile
from app.models.user import User
from app.models.profile.effective_risk_assessment import EffectiveRiskAssessment
from app.services.effective_risk_profile.calculation import compute_effective_risk_document
from app.services.effective_risk_profile.inputs import build_computation_input
from app.services.effective_risk_profile.merge import merge_computation_inputs

logger = logging.getLogger(__name__)


async def upsert_effective_risk_assessment(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    trigger_reason: str,
    as_of: Optional[date] = None,
) -> Optional[EffectiveRiskAssessment]:
    """
    Recompute and persist effective risk. Returns ``None`` if age (DOB) is missing or on failure.

    ``as_of`` is optional ``date`` for age (e.g. birthday batch run).

    **Incremental inputs:** If a prior assessment exists, only input fields relevant to
    ``trigger_reason`` (plus ``age`` from DOB) are taken from the DB; other inputs are copied
    from the previous payload. ``manual`` (and unknown triggers) replace all inputs from the DB.
    Calculations and scores are always recomputed from the merged inputs.
    """
    existing = (
        await db.execute(
            select(EffectiveRiskAssessment).where(EffectiveRiskAssessment.user_id == user_id)
        )
    ).scalar_one_or_none()
    previous_inputs = (existing.payload or {}).get("inputs") if existing and existing.payload else None

    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    profile = (
        await db.execute(
            select(PersonalFinanceProfile).where(PersonalFinanceProfile.user_id == user_id)
        )
    ).scalar_one_or_none()
    inv = (
        await db.execute(select(InvestmentProfile).where(InvestmentProfile.user_id == user_id))
    ).scalar_one_or_none()
    risk = (
        await db.execute(select(RiskProfile).where(RiskProfile.user_id == user_id))
    ).scalar_one_or_none()

    inp, err = build_computation_input(user, profile, inv, risk, as_of=as_of)
    if inp is None:
        logger.debug("Effective risk skipped for user %s: %s", user_id, err)
        return None

    try:
        merged_inp = merge_computation_inputs(previous_inputs, inp, trigger_reason)
    except (KeyError, TypeError, ValueError):
        logger.warning("Effective risk merge failed for user %s; using full DB inputs", user_id)
        merged_inp = inp

    try:
        doc = await asyncio.to_thread(compute_effective_risk_document, merged_inp)
    except Exception:
        logger.exception("Effective risk computation failed for user %s", user_id)
        return None

    out = doc.get("output") or {}
    calculations = doc.get("calculations") or {}
    eff = out.get("effective_risk_score")
    cap = calculations.get("risk_capacity_score_clamped")
    inputs = doc.get("inputs") or {}
    rw = inputs.get("risk_willingness")

    now = datetime.now(timezone.utc)
    if existing:
        existing.step_name = doc.get("step_name") or "risk_profile"
        existing.payload = doc
        existing.calculations = calculations
        existing.output = out
        existing.effective_risk_score = float(eff) if eff is not None else None
        existing.risk_capacity_score = float(cap) if cap is not None else None
        existing.risk_willingness = float(rw) if rw is not None else None
        existing.trigger_reason = trigger_reason[:64] if trigger_reason else None
        existing.computed_at = now
        await db.flush()
        await db.refresh(existing)
        return existing

    row = EffectiveRiskAssessment(
        user_id=user_id,
        step_name=doc.get("step_name") or "risk_profile",
        payload=doc,
        calculations=calculations,
        output=out,
        effective_risk_score=float(eff) if eff is not None else None,
        risk_capacity_score=float(cap) if cap is not None else None,
        risk_willingness=float(rw) if rw is not None else None,
        trigger_reason=trigger_reason[:64] if trigger_reason else None,
        computed_at=now,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


async def maybe_recalculate_effective_risk(
    db: AsyncSession,
    user_id: uuid.UUID,
    trigger_reason: str,
) -> None:
    """Best-effort recompute; never raises to callers (profile routes stay stable)."""
    try:
        await upsert_effective_risk_assessment(db, user_id, trigger_reason=trigger_reason)
    except Exception:
        logger.exception("maybe_recalculate_effective_risk failed user=%s", user_id)
