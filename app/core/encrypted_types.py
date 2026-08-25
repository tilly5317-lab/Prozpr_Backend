"""SQLAlchemy column types that encrypt their value at rest.

Aimed at the columns that hold the most sensitive data and are never queried by
content — third-party response blobs. Those two properties together are what
make transparent encryption cheap here: no index to preserve, no ``WHERE`` to
rewrite, so the change is invisible above the ORM.

Keys come from ``ENCRYPTION_KEY`` via the existing ``get_fernet()``. Until now
that key guarded exactly one value: the literal string ``b"pending"``. Anyone
reading the config reasonably assumed it was protecting something.

NOT for indexed or unique columns. Fernet is non-deterministic — the same input
encrypts differently every time — so a unique constraint over ciphertext does
not constrain anything and equality lookups cannot work. Those columns need a
separate deterministic blind-index column instead.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.types import Text, TypeDecorator

logger = logging.getLogger(__name__)

# Marks a value this type wrote, so decrypt can tell "we encrypted this" from
# "this row predates the migration" without a schema flag or a backfill gate.
_PREFIX = "enc:v1:"


class EncryptedJSON(TypeDecorator):
    """JSON in Python, Fernet-encrypted text in Postgres.

    Reads tolerate plaintext so the column can be switched on a live table and
    back-filled lazily: an existing row is returned as-is and re-encrypted the
    next time it is written. Without that, deploying this would 500 every read
    of every row written before it.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect) -> str | None:
        if value is None:
            return None
        plaintext = json.dumps(value, default=str, separators=(",", ":"))
        try:
            from app.core.security import get_fernet

            token = get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")
            return _PREFIX + token
        except Exception:
            # Storing the blob in the clear would silently defeat the control;
            # refusing the write is the honest failure. A missing/!invalid
            # ENCRYPTION_KEY is a deploy fault, not a runtime condition.
            logger.exception("Refusing to persist an unencrypted sensitive blob.")
            raise

    def process_result_value(self, value: Any, dialect) -> Any:
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            # Postgres JSON/JSONB column not yet migrated to text.
            return value
        if not isinstance(value, str):
            return value
        if not value.startswith(_PREFIX):
            return _loads_or_raw(value)  # pre-encryption row
        try:
            from app.core.security import get_fernet

            raw = get_fernet().decrypt(value[len(_PREFIX) :].encode("utf-8"))
            return json.loads(raw.decode("utf-8"))
        except Exception:
            # A rotated key must not make orders unreadable — the row's own
            # columns (state, amount, scheme) carry everything the app needs.
            logger.warning("Could not decrypt a sensitive blob; returning None.")
            return None


def _loads_or_raw(value: str) -> Any:
    try:
        return json.loads(value)
    except ValueError:
        return value
