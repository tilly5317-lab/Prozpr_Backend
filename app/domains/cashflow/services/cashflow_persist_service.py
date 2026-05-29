"""Cashflow plan run persistence: save engine output → ORM, retrieve latest, mark stale."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domains.cashflow.models.plan_run import (
    CashflowAnnualRow,
    CashflowFundFlowSummary,
    CashflowHeadline,
    CashflowMonthlyRow,
    CashflowPlanRun,
    CashflowPlanSummary,
)


async def persist_plan_run(
    db: AsyncSession,
    user_id: uuid.UUID,
    snapshot: Any,
    chat_session_id: uuid.UUID | None = None,
) -> CashflowPlanRun:
    """Map a GoalPlanningSnapshot to ORM rows and persist."""
    assumption_id = uuid.uuid4()

    run = CashflowPlanRun(
        user_id=user_id,
        chat_session_id=chat_session_id,
        engine_version=snapshot.engine_version,
        assumption_id=assumption_id,
        warnings=list(snapshot.warnings or []),
        computed_at=snapshot.computed_at or datetime.now(timezone.utc),
        is_stale=False,
    )
    db.add(run)
    await db.flush()

    h = snapshot.headline
    headline = CashflowHeadline(
        plan_run_id=run.id,
        user_id=user_id,
        years_to_last_goal=h.years_to_last_goal,
        last_goal_date=h.last_goal_date,
        last_fy_end_date=h.last_fy_end_date,
        number_of_goals=h.number_of_goals,
        corpus_today=float(h.corpus_today),
        total_corpus_required_today=float(h.total_corpus_required_today),
        surplus_or_shortfall_today=float(h.surplus_or_shortfall_today),
        corpus_closing=float(h.corpus_closing),
        total_shortfall_fv=float(h.total_shortfall_fv),
        total_funded_amount=float(h.total_funded_amount),
    )
    db.add(headline)

    ffs = snapshot.fund_flow_summary
    fund_flow = CashflowFundFlowSummary(
        plan_run_id=run.id,
        user_id=user_id,
        corpus_opening=float(ffs.corpus_opening),
        total_investments=float(ffs.total_investments),
        total_roi=float(ffs.total_roi),
        total_one_off_in=float(ffs.total_one_off_in),
        total_one_off_out=float(ffs.total_one_off_out),
        total_goals_paid=float(ffs.total_goals_paid),
        corpus_closing=float(ffs.corpus_closing),
        corpus_today=float(ffs.corpus_today),
        total_corpus_required_today=float(ffs.total_corpus_required_today),
        surplus_or_shortfall_today=float(ffs.surplus_or_shortfall_today),
    )
    db.add(fund_flow)

    if getattr(snapshot, "summary", None) is not None:
        s = snapshot.summary
        plan_summary = CashflowPlanSummary(
            plan_run_id=run.id,
            top_line=s.top_line,
            retirement_note=s.retirement_note,
            cashflow_note=s.cashflow_note,
            goals=[gb.model_dump() if hasattr(gb, "model_dump") else gb for gb in s.goals],
            risks=list(s.risks),
            next_steps=list(s.next_steps),
        )
        db.add(plan_summary)

    for row in snapshot.annual_cashflow:
        db.add(CashflowAnnualRow(
            plan_run_id=run.id,
            user_id=user_id,
            fy_end_date=row.fy_end_date,
            fy_label=row.fy_label,
            income=float(row.income),
            income_tax=float(row.income_tax),
            household_expense=float(row.household_expense),
            savings_pre_emi=float(row.savings_pre_emi),
            existing_mortgage_emi=float(row.existing_mortgage_emi),
            goal_mortgage_emi=float(row.goal_mortgage_emi),
            savings_post_emi=float(row.savings_post_emi),
            one_off_inflow=float(row.one_off_inflow),
            one_off_outflow=float(row.one_off_outflow),
            corpus_opening=float(row.corpus_opening),
            monthly_investment=float(row.monthly_investment),
            investment_returns=float(row.investment_returns),
            goal_payout=float(row.goal_payout),
            corpus_closing=float(row.corpus_closing),
            is_funded=row.is_funded,
        ))

    if snapshot.monthly_cashflow:
        for row in snapshot.monthly_cashflow:
            db.add(CashflowMonthlyRow(
                plan_run_id=run.id,
                user_id=user_id,
                month_end_date=row.month_end_date,
                fy_label=row.fy_label,
                income=float(row.income),
                income_tax=float(row.income_tax),
                household_expense=float(row.household_expense),
                savings_pre_emi=float(row.savings_pre_emi),
                existing_mortgage_emi=float(row.existing_mortgage_emi),
                goal_mortgage_emi=float(row.goal_mortgage_emi),
                savings_post_emi=float(row.savings_post_emi),
                one_off_inflow=float(row.one_off_inflow),
                one_off_outflow=float(row.one_off_outflow),
                corpus_opening=float(row.corpus_opening),
                monthly_investment=float(row.monthly_investment),
                investment_source=row.investment_source,
                investment_returns=float(row.investment_returns),
                goal_payout=float(row.goal_payout),
                corpus_closing=float(row.corpus_closing),
                is_funded=row.is_funded,
            ))

    await db.commit()
    await db.refresh(run)
    return run


async def get_latest_plan_run(
    db: AsyncSession, user_id: uuid.UUID,
) -> CashflowPlanRun | None:
    """Return the most recent plan run for a user with all children eagerly loaded."""
    stmt = (
        select(CashflowPlanRun)
        .where(CashflowPlanRun.user_id == user_id)
        .options(
            selectinload(CashflowPlanRun.headline),
            selectinload(CashflowPlanRun.fund_flow_summary),
            selectinload(CashflowPlanRun.plan_summary),
            selectinload(CashflowPlanRun.annual_rows),
            selectinload(CashflowPlanRun.monthly_rows),
        )
        .order_by(CashflowPlanRun.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def mark_stale(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Mark latest plan run as stale so next fetch triggers recomputation."""
    stmt = (
        update(CashflowPlanRun)
        .where(CashflowPlanRun.user_id == user_id, CashflowPlanRun.is_stale == False)  # noqa: E712
        .values(is_stale=True)
    )
    await db.execute(stmt)
    await db.commit()
