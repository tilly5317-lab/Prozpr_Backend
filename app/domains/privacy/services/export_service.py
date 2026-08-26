"""The right of access: everything we hold about one person, in one file.

Built on the same graph walk as erasure, so the export cannot quietly describe a
smaller system than the deletion empties. If a table is reachable for one it is
reachable for the other.
"""

from __future__ import annotations

import decimal
import logging
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.privacy.services.consent_service import (
    CURRENT_POLICY_VERSION,
    PURPOSE_NOTICE,
)
from app.domains.privacy.services.user_graph import collect_user_rows

logger = logging.getLogger(__name__)

#: The daily net-worth series alone is ~1.2k rows per user. Capped so an export
#: stays a document a person can actually open; the cap is reported in the file
#: rather than applied silently.
ROW_CAP_PER_TABLE = 5000

#: Columns that are ours, not the user's — hashes and reset material. Returning
#: them would turn a data export into a credential export.
_NEVER_EXPORT = frozenset(
    {"password_hash", "pin_reset_code_hash", "encrypted_access_token"}
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return "<binary>"
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


async def build_export(db: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    """A single JSON document: the notice, the consent position, and every row."""
    tables = await collect_user_rows(db, user_id, limit_per_table=ROW_CAP_PER_TABLE)

    data: dict[str, list[dict[str, Any]]] = {}
    truncated: list[str] = []
    for table, rows in sorted(tables.items()):
        if len(rows) >= ROW_CAP_PER_TABLE:
            truncated.append(table)
        data[table] = [
            {k: _jsonable(v) for k, v in row.items() if k not in _NEVER_EXPORT}
            for row in rows
        ]

    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "policy_version": CURRENT_POLICY_VERSION,
        "about": (
            "Everything Prozpr holds about you, one section per database table. "
            "Password and reset-code hashes are excluded — they are credentials, "
            "not your data."
        ),
        "purposes": {
            p.value: PURPOSE_NOTICE[p] for p in PURPOSE_NOTICE  # noqa: C416
        },
        "recipients": await _recipients(),
        "truncated_tables": truncated,
        "row_cap_per_table": ROW_CAP_PER_TABLE,
        "tables": data,
    }


async def _recipients() -> list[dict[str, str]]:
    """Who else has received this data.

    DPDP's access right covers the identities of other fiduciaries and
    processors the data was shared with, not just the data itself. Hard-coded
    because it is a factual claim about the deployment that must be reviewed
    when an integration is added — deriving it from config would let a new
    processor appear here without anyone noticing.
    """
    return [
        {"name": "Fintech Primitives (Cybrilla)", "purpose": "Order execution and KYC"},
        {"name": "CASParser", "purpose": "Reading your mutual fund statement"},
        {"name": "Anthropic", "purpose": "Generating chat answers"},
        {"name": "PostHog", "purpose": "Product analytics and error tracking"},
        {"name": "MSG91", "purpose": "Sending login OTPs"},
        {"name": "Resend / Zoho", "purpose": "Sending account email"},
        {"name": "Amazon Web Services", "purpose": "Hosting and statement storage"},
    ]


async def statement_archive(db: AsyncSession, user_id: uuid.UUID) -> list[dict[str, Any]]:
    """The user's archived CAS PDFs, listed for the export.

    Listed rather than embedded: these are multi-megabyte PDFs, and the download
    endpoint already issues short-lived presigned links.
    """
    rows = (
        await db.execute(
            text(
                "SELECT id, source_filename, uploaded_at "
                "FROM user_cas_documents WHERE user_id = :uid"
            ),
            {"uid": user_id},
        )
    ).mappings()
    return [{k: _jsonable(v) for k, v in dict(r).items()} for r in rows]
