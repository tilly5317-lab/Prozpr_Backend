"""Application service — `user_context.py`.

Encapsulates business logic consumed by FastAPI routers. Uses database sessions, optional external APIs, and other services; should remain free of route-specific HTTP details (status codes live in routers).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domains.portfolio.models.portfolio import Portfolio, PortfolioHolding
from app.domains.profile.models.investment_profile import InvestmentProfile
from app.domains.identity.models.user import User


async def load_user_for_ai(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    stmt = (
        select(User)
        .options(
            selectinload(User.personal_finance_profile),
            selectinload(User.risk_profile),
            selectinload(User.investment_profile).selectinload(
                InvestmentProfile.current_properties
            ),
            selectinload(User.effective_risk_assessment),
            selectinload(User.tax_profile),
            selectinload(User.financial_goals),
            selectinload(User.portfolios).selectinload(Portfolio.allocations),
            selectinload(User.portfolios)
            .selectinload(Portfolio.holdings)
            .selectinload(PortfolioHolding.fund_metadata),
            # MF transaction ledger (written by CAMS-CAS / AA ingest, see
            # ingestion.mf_aa_normalizer). Eager-loaded here because the
            # portfolio_query service walks `user.mf_transactions` synchronously
            # to compute portfolio XIRR (portfolio_query_service._compute_portfolio_xirr).
            # Under the async engine a lazy load there raises MissingGreenlet, so
            # the relationship must arrive with the rest of the AI user graph.
            selectinload(User.mf_transactions),
            selectinload(User.cashflow_assumptions),
            selectinload(User.cashflow_one_off_events),
            # Non-financial holdings (gold, FDs, unlisted shares…). Eager-loaded
            # so the cashflow readiness + input builder can sum them into the
            # user's "cash & assets" synchronously without a lazy load (which
            # raises MissingGreenlet under the async engine).
            selectinload(User.other_investments),
        )
        .where(User.id == user_id)
    )
    return (await db.execute(stmt)).scalar_one_or_none()
