"""SQLAlchemy ORM model — `user.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, Date, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.chat import ChatSession
    from app.models.chat_ai_module_run import ChatAiModuleRun
    from app.models.family_member import FamilyMember
    from app.models.fund import Fund
    from app.models.goals import FinancialGoal
    from app.models.ips import InvestmentPolicyStatement
    from app.models.linked_account import LinkedAccount
    from app.models.meeting_note import MeetingNote
    from app.models.mf import (
        MfAaImport,
        MfSipMandate,
        MfTransaction,
        PortfolioAllocationSnapshot,
        UserInvestmentList,
    )
    from app.models.notification import Notification
    from app.models.portfolio import Portfolio
    from app.models.stocks import StockTransaction
    from app.models.profile import (
        EffectiveRiskAssessment,
        InvestmentConstraint,
        InvestmentProfile,
        OtherInvestment,
        ReviewPreference,
        RiskProfile,
        TaxProfile,
        PersonalFinanceProfile,
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[Optional[str]] = mapped_column(
        String(320), unique=True, index=True, nullable=True
    )
    country_code: Mapped[str] = mapped_column(String(10), nullable=False)
    mobile: Mapped[str] = mapped_column(String(20), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    middle_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    pan: Mapped[Optional[str]] = mapped_column(String(20), unique=True, index=True, nullable=True)
    date_of_birth: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    occupation: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    family_status: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="GBP", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_onboarding_complete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    personal_finance_profile: Mapped[Optional["PersonalFinanceProfile"]] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    linked_accounts: Mapped[List["LinkedAccount"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    risk_profile: Mapped[Optional["RiskProfile"]] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    effective_risk_assessment: Mapped[Optional["EffectiveRiskAssessment"]] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    investment_profile: Mapped[Optional["InvestmentProfile"]] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    investment_constraint: Mapped[Optional["InvestmentConstraint"]] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    tax_profile: Mapped[Optional["TaxProfile"]] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    review_preference: Mapped[Optional["ReviewPreference"]] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    financial_goals: Mapped[List["FinancialGoal"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    portfolios: Mapped[List["Portfolio"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    chat_sessions: Mapped[List["ChatSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    meeting_notes: Mapped[List["MeetingNote"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    notifications: Mapped[List["Notification"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    investment_policy_statements: Mapped[List["InvestmentPolicyStatement"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    family_members_owned: Mapped[List["FamilyMember"]] = relationship(
        back_populates="owner",
        foreign_keys="FamilyMember.owner_id",
        cascade="all, delete-orphan",
    )
    funds: Mapped[List["Fund"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    other_investments: Mapped[List["OtherInvestment"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    mf_transactions: Mapped[List["MfTransaction"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    mf_aa_imports: Mapped[List["MfAaImport"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    mf_sip_mandates: Mapped[List["MfSipMandate"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    investment_lists: Mapped[List["UserInvestmentList"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    portfolio_allocation_snapshots: Mapped[List["PortfolioAllocationSnapshot"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    stock_transactions: Mapped[List["StockTransaction"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    ai_module_runs: Mapped[List["ChatAiModuleRun"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
