"""SQLAlchemy ORM model — `mfc_ft_order.py`.

One row per MF Central Financial Transaction: a purchase, redemption, switch,
SIP/STP/SWP registration or SIP pause/cancel that we asked the registrars to
execute.

This is the OUTBOUND half of the MFC integration. The inbound half
(``mfc_cas_requests``) records a request for data; this records a request to
MOVE MONEY, which is why it keeps more and deletes nothing.

Every FT is a four-call chain, minutes apart, with an OTP the investor types in
the middle:

    submit   -> MFC returns reqId                     (status: submitted)
    otp      -> MFC sends the investor a code         (status: otp_sent)
    consent  -> we relay the code; MFC accepts (202)  (status: consented)
    status   -> poll; the RTA settles asynchronously  (status: <RTA's own>)

Purchases add a fifth: ``ftPaymentUpdate`` once the money has actually moved.

The chain is why this is a table and not a request-scoped variable. ``req_id``
and ``client_ref_no`` are the only handles MFC will answer about an order, and
they are both minted in step one — losing them leaves an order that may well
execute at the RTA with nothing on our side able to name it. That is a worse
failure than never having placed it.

``request_payload`` keeps what we SENT, decrypted. When MFC rejects an order
the message names a field, and reconstructing the payload from our schema
afterwards is guesswork — bank details in particular are echoed back by the
RTA, not stored by us. It is the same reason ``fp_exec_orders`` keeps its raw
bodies, and it carries the same obligation: the payload holds an account number
and is encrypted at rest by the same mechanism.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class MfcFtKind(str, enum.Enum):
    """Which transaction family — decides the MFC endpoint and the payload shape."""

    PURCHASE = "purchase"  # distNewPurchase (lumpsum or first SIP)
    ADDITIONAL = "additional"  # distAdditionalPurchase (existing folio)
    REDEEM = "redeem"  # distRedeem
    SWITCH = "switch"  # distSwitch
    STP = "stp"  # distRegisterStp
    SWP = "swp"  # distRegisterSwp
    SIP_PAUSE = "sip_pause"  # submitSipPauseCancel, trxnType PSIP
    SIP_CANCEL = "sip_cancel"  # submitSipPauseCancel, trxnType CSIP


MFC_FT_KINDS: frozenset[str] = frozenset(k.value for k in MfcFtKind)


class MfcFtStatus(str, enum.Enum):
    """Where the order is in the chain.

    ``SUBMITTED`` through ``CONSENTED`` are OUR steps and mean only that MFC
    accepted the request. Nothing here implies the RTA executed anything —
    ``PROCESSING`` onward is read from ``getFtTransactionStatus``, and until
    that is polled the honest answer is "we asked".
    """

    DRAFT = "draft"
    SUBMITTED = "submitted"
    OTP_SENT = "otp_sent"
    CONSENTED = "consented"
    PROCESSING = "processing"
    SUCCESS = "success"
    REJECTED = "rejected"
    FAILED = "failed"


MFC_FT_STATUSES: frozenset[str] = frozenset(s.value for s in MfcFtStatus)

# Statuses past which an order can no longer be advanced. Kept as data because
# three separate call sites need the same answer to "is this still live".
MFC_FT_TERMINAL: frozenset[str] = frozenset(
    {MfcFtStatus.SUCCESS.value, MfcFtStatus.REJECTED.value, MfcFtStatus.FAILED.value}
)


class MfcFtOrder(Base):
    """One MF Central financial transaction and its whole lifecycle."""

    __tablename__ = "mfc_ft_orders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=MfcFtStatus.DRAFT.value
    )

    # Ours, and unique across every FT we ever send. MFC keys their own logging
    # on it, so a collision makes two investors' orders indistinguishable in a
    # support conversation about a payment that did not arrive.
    client_ref_no: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    # MFC's. String, not int — their FT samples show both `85938` and the
    # hyphenated `2559654-170718497`.
    req_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    otp_ref: Mapped[Optional[str]] = mapped_column(String(128))
    # The RTA's own transaction number, which is what an AMC's support desk asks
    # for. Only present once the order reaches them.
    user_trxn_no: Mapped[Optional[str]] = mapped_column(String(64))

    # What was ordered, denormalised so the list is readable without replaying
    # the payload. `amc` is MFC's code, not a name — see `mfc_masters`.
    amc: Mapped[Optional[str]] = mapped_column(String(10))
    folio: Mapped[Optional[str]] = mapped_column(String(40))
    isin: Mapped[Optional[str]] = mapped_column(String(20))
    to_isin: Mapped[Optional[str]] = mapped_column(String(20))
    scheme_name: Mapped[Optional[str]] = mapped_column(String(255))
    amount: Mapped[Optional[float]] = mapped_column(Numeric(18, 2))
    units: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    all_units: Mapped[Optional[bool]] = mapped_column()
    frequency: Mapped[Optional[str]] = mapped_column(String(10))
    start_date: Mapped[Optional[str]] = mapped_column(String(20))
    end_date: Mapped[Optional[str]] = mapped_column(String(20))
    installments: Mapped[Optional[int]] = mapped_column()

    pan: Mapped[Optional[str]] = mapped_column(String(20))
    otp_channel: Mapped[Optional[str]] = mapped_column(String(1))
    otp_destination: Mapped[Optional[str]] = mapped_column(String(320))

    # Sent and received, decrypted. See the module docstring on why.
    request_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    response_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    status_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)

    error: Mapped[Optional[str]] = mapped_column(Text)
    # The RTA's own words for the outcome. Deliberately stored verbatim
    # alongside our normalised `status`: theirs is what a support desk will
    # recognise, ours is what code branches on.
    rta_status: Mapped[Optional[str]] = mapped_column(String(120))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    consented_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_mfc_ft_orders_user_created", "user_id", "created_at"),
        Index("ix_mfc_ft_orders_status", "status"),
    )

    @property
    def is_terminal(self) -> bool:
        return self.status in MFC_FT_TERMINAL

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<MfcFtOrder {self.kind} {self.client_ref_no} "
            f"req={self.req_id} status={self.status}>"
        )
