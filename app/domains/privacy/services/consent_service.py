"""Consent ledger reads and writes.

The ledger is append-only (see the model docstring), so "what is this user's
current position on purpose P" is a query, not a column: take the most recent
row per purpose. That is the only place the append-only design costs anything,
and it buys a defensible history in exchange.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.privacy.models.consent import (
    OPTIONAL_PURPOSES,
    ConsentPurpose,
    ConsentRecord,
    PrivacyPolicyVersion,
)

#: Bumped whenever the notice text changes materially. Consent is recorded
#: against this, so a bump makes it visible that existing consent predates the
#: current notice rather than silently re-attributing it.
CURRENT_POLICY_VERSION = "2026.08.1"

#: What each purpose actually covers, in the user's terms. Served by
#: ``GET /privacy/notice`` so the notice and the enum can never drift apart —
#: a notice maintained separately from the code stops describing the system.
PURPOSE_NOTICE: dict[ConsentPurpose, dict[str, str]] = {
    ConsentPurpose.account_and_advisory: {
        "title": "Running your account and giving you advice",
        "detail": (
            "Your name, mobile, email, date of birth, PAN and the financial "
            "details you enter, used to run your account and produce your plan."
        ),
        "necessary": "yes",
    },
    ConsentPurpose.cas_ingestion: {
        "title": "Reading your mutual fund statement",
        "detail": (
            "Your consolidated account statement is sent to our statement "
            "parsing provider to extract your holdings, and the PDF is stored "
            "so you can download it again."
        ),
        "necessary": "no",
    },
    ConsentPurpose.llm_processing: {
        "title": "Answering your questions with AI",
        "detail": (
            "What you type in chat, plus a summary of your portfolio and goals, "
            "is sent to our AI provider to generate a reply. Your name, PAN, "
            "email and mobile are not sent."
        ),
        "necessary": "no",
    },
    ConsentPurpose.analytics: {
        "title": "Understanding how the app is used",
        "detail": (
            "Which screens you open and errors you hit, linked to an account "
            "identifier, used to find problems and improve the product."
        ),
        "necessary": "no",
    },
    ConsentPurpose.marketing_comms: {
        "title": "Sending you updates",
        "detail": "Occasional email about new features. Never required.",
        "necessary": "no",
    },
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def record_consent(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    purpose: ConsentPurpose,
    granted: bool,
    source: str = "web",
    ip: str | None = None,
    user_agent: str | None = None,
) -> ConsentRecord:
    """Append one entry. Never updates an existing row."""
    row = ConsentRecord(
        user_id=user_id,
        purpose=purpose,
        granted=granted,
        policy_version=CURRENT_POLICY_VERSION,
        source=source,
        ip=_truncate_ip(ip),
        user_agent=(user_agent or "")[:255] or None,
    )
    db.add(row)
    await db.flush()
    return row


def _truncate_ip(ip: str | None) -> str | None:
    """Keep the network, drop the host.

    Enough to show the consent came from a plausible place; not enough to be a
    location trail. Recording full IPs in a consent ledger would add a tracking
    surface to the very table meant to demonstrate restraint.
    """
    if not ip:
        return None
    if ":" in ip:  # IPv6 — keep the routing prefix only
        return ":".join(ip.split(":")[:4]) + "::/64"
    parts = ip.split(".")
    return ".".join(parts[:3]) + ".0/24" if len(parts) == 4 else None


async def current_consents(
    db: AsyncSession, user_id: uuid.UUID
) -> dict[ConsentPurpose, ConsentRecord | None]:
    """Latest entry per purpose. Missing purposes come back as ``None``."""
    rows = (
        (
            await db.execute(
                select(ConsentRecord)
                .where(ConsentRecord.user_id == user_id)
                .order_by(ConsentRecord.recorded_at.desc())
            )
        )
        .scalars()
        .all()
    )
    latest: dict[ConsentPurpose, ConsentRecord | None] = {p: None for p in ConsentPurpose}
    for row in rows:
        if latest.get(row.purpose) is None:
            latest[row.purpose] = row
    return latest


async def has_consent(
    db: AsyncSession, user_id: uuid.UUID, purpose: ConsentPurpose
) -> bool:
    """Whether ``purpose`` is currently permitted for this user.

    Necessary purposes default to True — they are the service itself, and
    refusing them is expressed by deleting the account, not by a toggle.
    Optional purposes default to **False**: silence is not consent, so a user
    who has never been asked must not be treated as having agreed.
    """
    if purpose not in OPTIONAL_PURPOSES:
        return True
    latest = (await current_consents(db, user_id)).get(purpose)
    return bool(latest and latest.granted)


async def ensure_current_policy_version(db: AsyncSession) -> PrivacyPolicyVersion:
    """Make sure the notice this build serves is recorded as a version.

    Self-registering so a deploy cannot end up with consent rows pointing at a
    version no table knows about.
    """
    existing = await db.get(PrivacyPolicyVersion, CURRENT_POLICY_VERSION)
    if existing is not None:
        return existing
    row = PrivacyPolicyVersion(
        version=CURRENT_POLICY_VERSION,
        effective_from=_now(),
        content_hash=notice_hash(),
        summary="Initial DPDP notice covering account, statement, AI, analytics and marketing purposes.",
    )
    db.add(row)
    await db.flush()
    return row


def notice_hash() -> str:
    """Stable hash of the served notice, so edits are detectable."""
    blob = "".join(
        f"{p.value}|{PURPOSE_NOTICE[p]['title']}|{PURPOSE_NOTICE[p]['detail']}"
        for p in ConsentPurpose
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
