"""FastAPI router — `onboarding.py`."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_effective_user
from app.domains.identity.models.onboarding_generation_job import (
    OnboardingGenerationJob,
)
from app.domains.identity.models.user import User
from app.domains.identity.services.onboarding_generation_service import (
    create_job as create_generation_job,
    generation_steps,
    get_latest_job as get_latest_generation_job,
    has_mf_transactions,
    has_running_job as has_running_generation_job,
    run_onboarding_generation,
)
from app.domains.profile.models import (
    OtherInvestment,
    OtherInvestmentStatus,
    PersonalFinanceProfile,
)
from app.domains.profile.services._effective_risk import (
    maybe_recalculate_effective_risk,
)
from app.domains.identity.schemas.onboarding import (
    CamsSkipRequest,
    CamsSkipResponse,
    GenerationStepInfo,
    OnboardingCompleteRequest,
    OnboardingGenerationStatusResponse,
    OnboardingProfileCreate,
    OnboardingProfileResponse,
    OtherAssetBulkCreate,
    OtherAssetResponse,
)

router = APIRouter(prefix="/onboarding", tags=["Onboarding"])

_USER_FIELDS = {
    "date_of_birth",
    "occupation",
    "assumed_lifespan_years",
    "family_status",
    "address",
    "currency",
}
_PFP_FIELDS = {
    "selected_goals",
    "custom_goals",
    "investment_horizon",
    "wealth_sources",
    "personal_values",
    "annual_income",
    "effective_tax_rate",
    "financial_assets",
    "equity_shares",
    "financial_liabilities_excl_mortgage",
    "monthly_household_expense",
    "starting_monthly_investment",
}


def _other_investment_to_legacy_response(row: OtherInvestment) -> OtherAssetResponse:
    return OtherAssetResponse(
        id=row.id,
        asset_name=row.investment_name,
        asset_type=row.investment_type,
        current_value=float(row.present_value),
    )


def _profile_to_response(
    user: User, profile: PersonalFinanceProfile
) -> OnboardingProfileResponse:
    return OnboardingProfileResponse(
        user_id=user.id,
        date_of_birth=user.date_of_birth,
        assumed_lifespan_years=user.assumed_lifespan_years,
        occupation=user.occupation,
        family_status=user.family_status,
        address=user.address,
        currency=user.currency,
        selected_goals=profile.selected_goals or [],
        custom_goals=profile.custom_goals or [],
        investment_horizon=profile.investment_horizon,
        wealth_sources=profile.wealth_sources or [],
        personal_values=profile.personal_values or [],
        annual_income=float(profile.annual_income)
        if profile.annual_income is not None
        else None,
        effective_tax_rate=(
            float(profile.effective_tax_rate)
            if profile.effective_tax_rate is not None
            else None
        ),
        financial_assets=(
            float(profile.financial_assets)
            if profile.financial_assets is not None
            else None
        ),
        equity_shares=(
            float(profile.equity_shares)
            if profile.equity_shares is not None
            else None
        ),
        financial_liabilities_excl_mortgage=(
            float(profile.financial_liabilities_excl_mortgage)
            if profile.financial_liabilities_excl_mortgage is not None
            else None
        ),
        monthly_household_expense=(
            float(profile.monthly_household_expense)
            if profile.monthly_household_expense is not None
            else None
        ),
        starting_monthly_investment=(
            float(profile.starting_monthly_investment)
            if profile.starting_monthly_investment is not None
            else None
        ),
        updated_at=profile.updated_at,
    )


@router.post("/profile", response_model=OnboardingProfileResponse)
async def save_onboarding_profile(
    payload: OnboardingProfileCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(PersonalFinanceProfile).where(
        PersonalFinanceProfile.user_id == current_user.id
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    user_stmt = select(User).where(User.id == current_user.id)
    user = (await db.execute(user_stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    payload_data = payload.model_dump(exclude_unset=existing is not None)
    user_updates = {
        k: payload_data.pop(k) for k in list(payload_data) if k in _USER_FIELDS
    }
    for field, value in user_updates.items():
        if field == "occupation" and value is not None:
            value = value.strip()[:100] if value.strip() else None
        # `users.currency` is NOT NULL with default 'GBP'. The onboarding
        # schema carries `currency: Optional[str] = None`, so when the client
        # omits it on first-time onboarding model_dump() returns None and
        # SQLAlchemy would write that NULL over the default. Skip it.
        if field == "currency" and value is None:
            continue
        setattr(user, field, value)

    if existing:
        for field, value in payload_data.items():
            if field in _PFP_FIELDS:
                setattr(existing, field, value)
        profile = existing
    else:
        profile = PersonalFinanceProfile(user_id=current_user.id, **payload_data)
        db.add(profile)

    await db.commit()
    await db.refresh(profile)
    try:
        await maybe_recalculate_effective_risk(
            db, current_user.id, "onboarding_profile_update"
        )
        await db.commit()
    except Exception:
        await db.rollback()

    # Onboarding writes the core cashflow inputs (DOB, income, expense, assets,
    # tax) — invalidate any cached plan run so it recomputes on fresh values.
    from app.domains.cashflow.services.cashflow_persist_service import (
        mark_stale as mark_cashflow_stale,
    )

    await mark_cashflow_stale(db, current_user.id)

    return _profile_to_response(user, profile)


@router.get("/profile", response_model=OnboardingProfileResponse)
async def get_onboarding_profile(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(PersonalFinanceProfile).where(
        PersonalFinanceProfile.user_id == current_user.id
    )
    profile = (await db.execute(stmt)).scalar_one_or_none()
    user_stmt = select(User).where(User.id == current_user.id)
    user = (await db.execute(user_stmt)).scalar_one_or_none()
    if not profile or not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found"
        )

    return _profile_to_response(user, profile)


@router.get("/other-assets", response_model=list[OtherAssetResponse])
async def get_other_assets(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Read back the user's saved non-financial holdings so the onboarding form
    can prefill them when the user returns to edit."""
    stmt = (
        select(OtherInvestment)
        .where(
            OtherInvestment.user_id == current_user.id,
            OtherInvestment.status == OtherInvestmentStatus.ACTIVE,
        )
        .order_by(OtherInvestment.created_at)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [_other_investment_to_legacy_response(r) for r in rows]


@router.post("/other-assets", response_model=list[OtherAssetResponse])
async def save_other_assets(
    payload: OtherAssetBulkCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Full-replace write of the user's "other assets" list."""
    await db.execute(
        delete(OtherInvestment).where(OtherInvestment.user_id == current_user.id)
    )
    # `other_investments` has investment_type AND as_of_date as NOT NULL. The
    # onboarding UI only collects a single free-text description (sent as
    # asset_name) plus a value, so derive a non-null type from the description
    # and stamp today's date — otherwise the INSERT fails the NOT NULL checks.
    today = datetime.now(timezone.utc).date()
    rows = [
        OtherInvestment(
            user_id=current_user.id,
            investment_name=asset.asset_name.strip()[:200],
            investment_type=(
                (asset.asset_type or asset.asset_name).strip()[:50] or "OTHER"
            ),
            present_value=asset.current_value or 0,
            as_of_date=today,
            status=OtherInvestmentStatus.ACTIVE,
        )
        for asset in payload.assets
        if asset.asset_name and asset.asset_name.strip()
    ]
    db.add_all(rows)
    await db.commit()
    for row in rows:
        await db.refresh(row)
    return [_other_investment_to_legacy_response(r) for r in rows]


@router.post("/complete", status_code=status.HTTP_204_NO_CONTENT)
async def complete_onboarding(
    payload: OnboardingCompleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    user_stmt = select(User).where(User.id == current_user.id)
    user = (await db.execute(user_stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    # The new-signup team notification no longer fires here — it now fires the
    # moment the user submits the name / PIN / email setup page (`/auth/signup`),
    # so the team hears about a signup even if the user abandons before CAMS.
    user.is_onboarding_complete = payload.is_complete
    user.updated_at = datetime.now(timezone.utc)
    await db.commit()


@router.post("/cams-skip", response_model=CamsSkipResponse)
async def set_cams_skipped(
    payload: CamsSkipRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Record (or clear) the "I'll do this later" choice on the CAMS step.

    CAMS is no longer compulsory to finish onboarding: skipping stamps
    ``users.cams_skipped_at`` so the backend-driven resume resolver stops
    landing the user back on ``/cams-upload``. Every CAMS entry point in the app
    stays available, and a successful statement import clears the stamp (see
    ``mf_ingest_router.ingest_cams_statement_pdf``).
    """
    user = (
        await db.execute(select(User).where(User.id == current_user.id))
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    user.cams_skipped_at = datetime.now(timezone.utc) if payload.skipped else None
    await db.commit()
    return CamsSkipResponse(
        cams_skipped=user.cams_skipped_at is not None,
        skipped_at=user.cams_skipped_at,
    )


# ─────────────── "Generate my portfolio" personalisation job ───────────────


# A running job whose row hasn't been touched for this long is considered
# stalled (e.g. the process died mid-job or lost its DB connection for good) —
# report it failed so the loading page offers a retry instead of hanging.
_GENERATION_STALL_S = 180


def _generation_status(
    job: OnboardingGenerationJob | None,
    steps_spec: tuple[tuple[str, str], ...],
) -> OnboardingGenerationStatusResponse:
    """Map a job row (or its absence) to the polled status shape.

    The checklist derives from ``steps_spec`` — the steps this user's job will
    actually run (see ``generation_steps``; a user with no imported holdings has
    no net-worth history to build, so that row is absent rather than shown
    ticking over nothing). Everything before the current phase is done, the
    current phase is active while the job runs, and a successful job marks every
    step done.
    """
    keys = [k for k, _ in steps_spec]
    if job is None:
        steps = [
            GenerationStepInfo(key=k, label=label, state="pending")
            for k, label in steps_spec
        ]
        return OnboardingGenerationStatusResponse(status="none", steps=steps)

    job_status = job.status
    if job_status in ("pending", "running") and job.updated_at is not None:
        updated = job.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        age_s = (datetime.now(timezone.utc) - updated).total_seconds()
        if age_s > _GENERATION_STALL_S:
            job_status = "failed"

    if job_status == "success" or job.phase == "done":
        active_idx = len(keys)
    elif job.phase in keys:
        active_idx = keys.index(job.phase)
    else:  # "queued"
        active_idx = 0

    steps = []
    for i, (k, label) in enumerate(steps_spec):
        if i < active_idx:
            state = "done"
        elif i == active_idx and job_status == "running":
            state = "active"
        else:
            state = "pending"
        steps.append(GenerationStepInfo(key=k, label=label, state=state))

    stalled = job_status == "failed" and job.status != "failed"
    return OnboardingGenerationStatusResponse(
        status=job_status,  # type: ignore[arg-type] — constrained by the job writer
        phase=job.phase,
        progress_pct=float(job.progress_pct or 0),
        message="Failed: the setup stalled. Please try again." if stalled else job.message,
        steps=steps,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


@router.post("/generate", response_model=OnboardingGenerationStatusResponse)
async def start_generation(
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Kick off the post-signup personalisation job and return its status.

    Idempotent: if a job is already pending/running its status is returned
    instead of starting a duplicate, so the button and the loading page can
    both call this safely.
    """
    steps_spec = generation_steps(await has_mf_transactions(db, current_user.id))
    running = await has_running_generation_job(db, current_user.id)
    if running is not None:
        current = _generation_status(running, steps_spec)
        if current.status != "failed":
            return current
        # The "running" row is stalled (see _GENERATION_STALL_S) — mark it
        # failed for the record and fall through to start a fresh job.
        running.status = "failed"
        running.finished_at = datetime.now(timezone.utc)
        await db.commit()
    job = await create_generation_job(db, current_user.id)
    background.add_task(run_onboarding_generation, current_user.id, job.id)
    return _generation_status(job, steps_spec)


@router.get("/generate/status", response_model=OnboardingGenerationStatusResponse)
async def generation_status(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Latest personalisation-job status — the loading page polls this."""
    steps_spec = generation_steps(await has_mf_transactions(db, current_user.id))
    return _generation_status(
        await get_latest_generation_job(db, current_user.id), steps_spec
    )
