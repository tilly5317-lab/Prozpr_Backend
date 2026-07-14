"""ORM models — FP execution: one account row per user, one row per order.

Statuses are stored as FP's own state strings verbatim (``under_review``,
``created``, …) — no local enum to keep in sync with FP's lifecycle. The
account row also tracks the KYC readiness check (Pre-Verification): it is
created at signup with ``kyc_status='pending'`` and flips to ``completed`` /
``failed`` once the user submits their PAN on the KYC page.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.database import Base


class FpExecAccount(Base):
    """FP-side ids minted by one-time setup (investor profile + contact
    details + bank + MF investment account) plus the KYC readiness state.
    One row per user."""

    __tablename__ = "fp_exec_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    fp_investor_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    fp_investment_account_id: Mapped[Optional[str]] = mapped_column(
        String(80), nullable=True
    )
    holder_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    pan: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    bank_account_masked: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True
    )

    # KYC readiness (Pre-Verification): pending -> submitted -> completed/failed.
    kyc_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    kyc_pv_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    kyc_checked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    raw: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FpExecOrder(Base):
    """One FP order: lumpsum (``mfp_…``) or SIP plan (``mfpp_…``)."""

    __tablename__ = "fp_exec_orders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    fp_id: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # LUMPSUM | SIP
    state: Mapped[str] = mapped_column(String(40), nullable=False)  # FP verbatim

    scheme_code: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    scheme_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    isin: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)

    installment_day: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    number_of_installments: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )

    raw: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
