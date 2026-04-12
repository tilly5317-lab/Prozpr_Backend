from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import Base, _get_engine, _get_session_factory
from app.models import (  # noqa: F401 - ensure metadata is loaded
    AssetAllocationConstraint,
    ChatMessage,
    ChatSession,
    CompanyMetadata,
    FamilyMember,
    FinancialGoal,
    GoalContribution,
    GoalHolding,
    InvestmentConstraint,
    InvestmentPolicyStatement,
    InvestmentProfile,
    LinkedAccount,
    MeetingNote,
    MeetingNoteItem,
    MfFundMetadata,
    MfNavHistory,
    MfSipMandate,
    MfTransaction,
    Notification,
    OtherInvestment,
    Portfolio,
    PortfolioAllocation,
    PortfolioAllocationSnapshot,
    PortfolioHistory,
    PortfolioHolding,
    RebalancingRecommendation,
    ReviewPreference,
    RiskProfile,
    StockPriceHistory,
    StockTransaction,
    TaxProfile,
    User,
    UserInvestmentList,
    UserProfile,
)
from app.models.chat import ChatMessageRole, ChatSessionStatus
from app.models.goals import GoalPriority, GoalStatus, GoalType
from app.models.profile import OtherInvestmentStatus
from app.models.linked_account import LinkedAccountStatus, LinkedAccountType
from app.models.meeting_note import MeetingNoteItemType
from app.models.rebalancing import RebalancingStatus
from app.utils.security import hash_password


DATA_PATH = ROOT_DIR / "app" / "data" / "dummy_data.json"
DEFAULT_PASSWORD = "Test@1234"


def _load_users() -> list[dict]:
    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    users = payload.get("users", [])
    if len(users) != 10:
        raise ValueError("dummy_data.json must contain exactly 10 users.")
    return users


async def _truncate_all_tables() -> None:
    engine = _get_engine()
    table_names = [table.name for table in Base.metadata.sorted_tables]
    joined = ", ".join(f'"{name}"' for name in table_names)
    query = f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE;"
    async with engine.begin() as conn:
        await conn.execute(text(query))


def _risk_capacity_from_level(level: int) -> str:
    if level <= 1:
        return "low"
    if level == 2:
        return "medium"
    return "high"


def _horizon_from_level(level: int) -> str:
    if level <= 1:
        return "5 years"
    if level == 2:
        return "10 years"
    return "15 years"


def _alloc_for_level(level: int) -> dict[str, float]:
    if level <= 1:
        return {"Equity Large Cap": 30.0, "Equity Mid Cap": 10.0, "Debt": 50.0, "Gold": 10.0}
    if level == 2:
        return {"Equity Large Cap": 40.0, "Equity Mid Cap": 20.0, "Debt": 30.0, "Gold": 10.0}
    return {"Equity Large Cap": 45.0, "Equity Mid Cap": 25.0, "Debt": 20.0, "Gold": 10.0}


async def _seed(session: AsyncSession, rows: list[dict]) -> None:
    now = datetime.now(timezone.utc)
    users: list[User] = []
    portfolios: list[Portfolio] = []
    goals: list[FinancialGoal] = []
    meetings: list[MeetingNote] = []

    for row in rows:
        idx = int(row["index"])
        risk_level = int(row["risk_level"])
        risk_capacity = _risk_capacity_from_level(risk_level)
        horizon = _horizon_from_level(risk_level)
        annual_income = float(row["annual_income"])
        liabilities = float(row["liabilities"])
        portfolio_value = float(row["portfolio_value"])
        target_amount = float(row["target_amount"])

        user = User(
            email=row["email"],
            country_code=row["country_code"],
            mobile=row["mobile"],
            phone=row["phone"],
            password_hash=hash_password(DEFAULT_PASSWORD),
            first_name=row["first_name"],
            last_name=row["last_name"],
            is_active=True,
            is_onboarding_complete=True,
        )
        session.add(user)
        await session.flush()
        users.append(user)

        profile = UserProfile(
            user_id=user.id,
            date_of_birth=date(1988 + (idx % 8), (idx % 12) + 1, (idx % 27) + 1),
            selected_goals=["retirement", "wealth_creation"],
            custom_goals=[],
            investment_horizon=horizon,
            annual_income_min=annual_income * 0.9,
            annual_income_max=annual_income * 1.1,
            annual_expense_min=annual_income * 0.35,
            annual_expense_max=annual_income * 0.55,
            occupation="Salaried",
            family_status="Married" if idx % 2 == 0 else "Single",
            wealth_sources=["salary", "bonus"],
            personal_values=["stability", "growth"],
            address=f"Dummy Address {idx + 1}, New Delhi",
            currency="INR",
        )
        session.add(profile)

        session.add(
            RiskProfile(
                user_id=user.id,
                risk_level=risk_level,
                risk_capacity=risk_capacity,
                investment_experience="intermediate",
                investment_horizon=horizon,
                drop_reaction="buy_more" if risk_level >= 2 else "hold",
                max_drawdown=10 + (risk_level * 5),
                comfort_assets=["equity", "debt", "gold"],
            )
        )

        session.add(
            InvestmentProfile(
                user_id=user.id,
                objectives=["wealth_creation", "retirement"],
                detailed_goals=[{"name": "Retirement", "target": target_amount}],
                portfolio_value=portfolio_value,
                monthly_savings=max(20000, (annual_income / 12) * 0.2),
                target_corpus=target_amount,
                target_timeline=horizon,
                annual_income=annual_income,
                retirement_age=60,
                investable_assets=portfolio_value * 1.2,
                total_liabilities=liabilities,
                property_value=4500000 + idx * 200000,
                mortgage_amount=max(0, liabilities - 100000),
                expected_inflows=annual_income * 0.15,
                regular_outgoings=annual_income * 0.45,
                planned_major_expenses=annual_income * 0.25,
                emergency_fund=annual_income * 0.4,
                emergency_fund_months="6 months",
                liquidity_needs="Maintain healthy emergency liquidity buffer.",
                income_needs=annual_income * 0.35,
                is_multi_phase_horizon=False,
                phase_description="Single accumulation phase.",
                total_horizon=horizon,
            )
        )

        constraint = InvestmentConstraint(
            user_id=user.id,
            permitted_assets=["equity", "debt", "gold"],
            prohibited_instruments=["crypto_futures"],
            is_leverage_allowed=False,
            is_derivatives_allowed=False,
            diversification_notes="No single asset bucket should dominate the portfolio.",
        )
        session.add(constraint)
        await session.flush()

        for asset_class, min_alloc, max_alloc in [
            ("equity_large_cap", 20.0, 60.0),
            ("equity_mid_cap", 5.0, 30.0),
            ("debt", 15.0, 70.0),
            ("gold", 0.0, 20.0),
        ]:
            session.add(
                AssetAllocationConstraint(
                    constraint_id=constraint.id,
                    asset_class=asset_class,
                    min_allocation=min_alloc,
                    max_allocation=max_alloc,
                )
            )

        session.add(
            TaxProfile(
                user_id=user.id,
                income_tax_rate=30.0 if annual_income > 1500000 else 20.0,
                capital_gains_tax_rate=10.0,
                notes="Dummy tax profile seeded for testing.",
            )
        )
        session.add(
            ReviewPreference(
                user_id=user.id,
                frequency="quarterly",
                triggers=["drawdown", "goal_deviation"],
                update_process="Advisor review + client approval",
            )
        )
        session.add(
            LinkedAccount(
                user_id=user.id,
                account_type=LinkedAccountType.mutual_fund,
                provider_name="Dummy AMC",
                account_identifier=f"DUMMY-ACC-{idx:03d}",
                encrypted_access_token="encrypted_dummy_token",
                status=LinkedAccountStatus.active,
                metadata_json={"source": "seed"},
                linked_at=now - timedelta(days=30 + idx),
                last_synced_at=now - timedelta(days=idx),
            )
        )
        session.add(
            OtherInvestment(
                user_id=user.id,
                investment_name="Residential Property",
                investment_type="REAL_ESTATE",
                present_value=4500000 + idx * 200000,
                as_of_date=date.today(),
                status=OtherInvestmentStatus.ACTIVE,
                notes=json.dumps({"city": "Delhi", "usage": "self_occupied"}),
            )
        )

        goal = FinancialGoal(
            user_id=user.id,
            goal_name="Retirement Corpus",
            goal_type=GoalType.RETIREMENT,
            present_value_amount=target_amount,
            inflation_rate=6.0,
            target_date=date.today() + timedelta(days=365 * (10 + (idx % 6))),
            priority=GoalPriority.PRIMARY,
            status=GoalStatus.ACTIVE,
            notes="Primary long-term retirement goal.",
        )
        session.add(goal)
        await session.flush()
        goals.append(goal)

        session.add(
            GoalContribution(
                goal_id=goal.id,
                amount=max(10000, annual_income * 0.06 / 12),
                note="Monthly SIP contribution",
            )
        )
        session.add(
            GoalHolding(
                goal_id=goal.id,
                fund_name="AILAX Balanced Growth Fund",
                category="Hybrid",
                invested_amount=portfolio_value * 0.25,
                current_value=portfolio_value * 0.29,
                gain_percentage=14.5,
            )
        )

        portfolio = Portfolio(
            user_id=user.id,
            name="Primary",
            total_value=portfolio_value,
            total_invested=portfolio_value * 0.82,
            total_gain_percentage=12.6,
            is_primary=True,
        )
        session.add(portfolio)
        await session.flush()
        portfolios.append(portfolio)

        alloc_map = _alloc_for_level(risk_level)
        for asset_class, pct in alloc_map.items():
            session.add(
                PortfolioAllocation(
                    portfolio_id=portfolio.id,
                    asset_class=asset_class,
                    allocation_percentage=pct,
                    amount=portfolio_value * (pct / 100.0),
                    performance_percentage=10.0 + (idx % 5),
                )
            )

        session.add(
            PortfolioHolding(
                portfolio_id=portfolio.id,
                instrument_name="AILAX Large Cap Fund",
                instrument_type="mutual_fund",
                ticker_symbol=f"ALCF{idx}",
                quantity=120 + idx * 3,
                average_cost=95.0 + idx,
                current_price=112.0 + idx,
                current_value=portfolio_value * 0.35,
                allocation_percentage=35.0,
                exchange="NSE",
                expense_ratio=0.0095,
                return_1y=14.2,
                return_3y=11.1,
                return_5y=12.4,
            )
        )
        session.add(
            PortfolioHistory(
                portfolio_id=portfolio.id,
                recorded_date=date.today() - timedelta(days=30),
                total_value=portfolio_value * 0.96,
            )
        )
        session.add(
            RebalancingRecommendation(
                portfolio_id=portfolio.id,
                status=RebalancingStatus.pending,
                recommendation_data={"action": "rebalance", "confidence": "medium"},
                reason="Seeded recommendation",
            )
        )

        chat_session = ChatSession(
            user_id=user.id,
            title="Portfolio Review",
            status=ChatSessionStatus.active,
        )
        session.add(chat_session)
        await session.flush()
        session.add(
            ChatMessage(
                session_id=chat_session.id,
                role=ChatMessageRole.user,
                content="Review my portfolio",
            )
        )
        session.add(
            ChatMessage(
                session_id=chat_session.id,
                role=ChatMessageRole.assistant,
                content="Sure, I can help with a portfolio review.",
            )
        )

        meeting = MeetingNote(
            user_id=user.id,
            title="Quarterly Review Meeting",
            meeting_date=now - timedelta(days=7),
            is_mandate_approved=True,
        )
        session.add(meeting)
        await session.flush()
        meetings.append(meeting)
        session.add(
            MeetingNoteItem(
                meeting_note_id=meeting.id,
                item_type=MeetingNoteItemType.summary,
                role="advisor",
                content="Client aligned with risk profile and long-term goals.",
                sort_order=1,
            )
        )

        session.add(
            Notification(
                user_id=user.id,
                title="Portfolio Review Due",
                message="Your quarterly review is ready.",
                notification_type="review",
                is_read=False,
                action_url="/portfolio",
            )
        )
        session.add(
            InvestmentPolicyStatement(
                user_id=user.id,
                version=1,
                status="approved",
                content={"risk_profile": risk_capacity, "horizon": horizon},
            )
        )
    for i in range(0, len(users) - 1):
        session.add(
            FamilyMember(
                owner_id=users[i].id,
                member_user_id=users[i + 1].id,
                nickname=f"{users[i + 1].first_name} Family",
                email=users[i + 1].email,
                phone=users[i + 1].phone,
                relationship_type="spouse" if i % 2 == 0 else "sibling",
                status="active",
            )
        )

    await session.commit()


async def main() -> None:
    rows = _load_users()
    await _truncate_all_tables()
    session_factory = _get_session_factory()
    async with session_factory() as session:
        await _seed(session, rows)
    print("Database reset complete. Inserted 10 dummy users and related records.")
    print(f"Login password for all dummy users: {DEFAULT_PASSWORD}")


if __name__ == "__main__":
    asyncio.run(main())
