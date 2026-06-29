"""SQLAlchemy ORM — additional-investment engine runs and BUY-only children.

One ``additional_investment_runs`` row per execution of the additional-investment
engine (``AI_Agents/src/additional_investment``). Every run deploys fresh money
(lumpsum or monthly SIP) toward the persisted PRACTICAL allocation run it is
derived from — ``source_allocation_run_id`` references
``practical_asset_allocation_runs.id`` and supplies the per-bucket subgroup
amounts the engine splits the deploy amount across.

Children:

- ``additional_investment_targets`` — per-subgroup deploy target (ratio + rupees),
  one row per ``SubgroupTarget``.
- ``additional_investment_buys``    — execution-ready BUY instructions, one row per ``FundBuy``.

BUY-only, write-once domain: NO status lifecycle and — unlike ``rebalancing_runs`` —
NO tax / rounding / exit-load columns (the engine carries no tax-lot arithmetic).
Money is ``Numeric(18, 2)`` fed plain floats (the allocation family), not ``Decimal``.

The ``TargetBucket`` / ``Cadence`` enums are declared LOCALLY (mirroring how the
rebalancing ORM declares its own enums) rather than imported from the engine: this
module is imported by Alembic's ``env.py`` and ``app/all_models.py``, which must not
depend on ``AI_Agents/src`` being on ``sys.path``. Their values are kept identical to
``AI_Agents/src/additional_investment/models.py`` so the persist service can round-trip.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.domains.chat.models.chat import ChatSession
    from app.domains.identity.models.user import User
    from app.domains.portfolio.models.portfolio import Portfolio
    from app.domains.practical_asset_allocation.models.run import (
        PracticalAssetAllocationRun,
    )


class TargetBucket(str, enum.Enum):
    """Subgroup-weighting branch the engine took. Values mirror
    ``additional_investment.models.TargetBucket`` exactly."""

    SHORT_TERM = "short_term"
    MEDIUM_TERM = "medium_term"
    LONG_TERM = "long_term"


class Cadence(str, enum.Enum):
    """Deploy cadence. Values mirror ``additional_investment.models.Cadence`` exactly."""

    LUMPSUM = "lumpsum"
    SIP_MONTHLY = "sip_monthly"


class AdditionalInvestmentRun(Base):
    """One execution of the additional-investment engine for a user's portfolio."""

    __tablename__ = "additional_investment_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chat_session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_allocation_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("practical_asset_allocation_runs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    engine_version: Mapped[str] = mapped_column(String(40), nullable=False)

    target_bucket: Mapped[TargetBucket] = mapped_column(
        SAEnum(
            TargetBucket,
            name="additional_investment_target_bucket_enum",
            create_constraint=True,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
    )
    cadence: Mapped[Cadence] = mapped_column(
        SAEnum(
            Cadence,
            name="additional_investment_cadence_enum",
            create_constraint=True,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
    )

    deploy_amount_inr: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    deployed_inr: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    undeployed_inr: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)

    request_input: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )
    user_question: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    used_cached_allocation: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship()
    portfolio: Mapped["Portfolio"] = relationship()
    chat_session: Mapped[Optional["ChatSession"]] = relationship()
    source_allocation_run: Mapped["PracticalAssetAllocationRun"] = relationship()

    targets: Mapped[List["AdditionalInvestmentTarget"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    buys: Mapped[List["AdditionalInvestmentBuy"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class AdditionalInvestmentTarget(Base):
    """Per-subgroup deploy target for a run (mirrors engine ``SubgroupTarget``)."""

    __tablename__ = "additional_investment_targets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("additional_investment_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subgroup: Mapped[str] = mapped_column(String(80), nullable=False)
    ratio: Mapped[float] = mapped_column(Float, nullable=False)
    target_inr: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)

    run: Mapped["AdditionalInvestmentRun"] = relationship(back_populates="targets")


class AdditionalInvestmentBuy(Base):
    """One BUY instruction emitted by the engine (mirrors engine ``FundBuy``)."""

    __tablename__ = "additional_investment_buys"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("additional_investment_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    recommended_fund: Mapped[str] = mapped_column(String(255), nullable=False)
    isin: Mapped[str] = mapped_column(String(20), nullable=False)
    sub_category: Mapped[str] = mapped_column(String(80), nullable=False)
    asset_subgroup: Mapped[str] = mapped_column(String(80), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    scheme_code: Mapped[str] = mapped_column(String(40), nullable=False)
    amount_inr: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    monthly_amount_inr: Mapped[Optional[float]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    run: Mapped["AdditionalInvestmentRun"] = relationship(back_populates="buys")
