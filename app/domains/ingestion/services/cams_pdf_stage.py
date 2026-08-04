"""S3 staging for CAS PDFs served to casparser via ``pdf_url``.

casparser's edge caps multipart uploads plan-dependently (~1.8 MB), but the
``pdf_url`` request mode has no size limit (their support, 2026-08-04). For a
large statement we upload the bytes to a private S3 bucket, hand casparser a
short-lived presigned GET URL, and delete the object the moment parsing
returns.

Security model: the bucket stays fully private (Block Public Access on);
access is only via the presigned URL, which embeds an unguessable
SigV4 signature and expires after ``_URL_TTL_SECONDS``. Objects are deleted in
a ``finally`` right after the parse; configure a 1-day lifecycle expiry on the
prefix as the safety net for crashed processes. Statements contain
PII/financials: never log presigned URLs.

``boto3`` is imported lazily so the app boots without it installed (same
pattern as the old local ``casparser`` dependency); staging is inert unless
``CAMS_STAGE_S3_BUCKET`` is configured. boto3 is synchronous — every S3 call
runs in a worker thread to keep the event loop free.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from typing import Any, Optional

from app.core.config import Settings

logger = logging.getLogger(__name__)

_URL_TTL_SECONDS = 10 * 60  # casparser fetches within seconds; 10 min is generous
_KEY_PREFIX = "cams-stage/"

_s3_client: Any = None


def _get_s3() -> Any:
    global _s3_client
    if _s3_client is None:
        import boto3  # heavy optional dependency — keep the import lazy

        _s3_client = boto3.client("s3")  # region/creds from the default chain
    return _s3_client


async def _put_pdf(bucket: str, key: str, data: bytes) -> None:
    put_kwargs: dict[str, Any] = {
        "Bucket": bucket,
        "Key": key,
        "Body": data,
        "ContentType": "application/pdf",
    }
    kms_key = Settings.get_cams_stage_kms_key_id()
    if kms_key:
        put_kwargs["ServerSideEncryption"] = "aws:kms"
        put_kwargs["SSEKMSKeyId"] = kms_key
    else:
        put_kwargs["ServerSideEncryption"] = "AES256"
    s3 = _get_s3()
    await asyncio.to_thread(lambda: s3.put_object(**put_kwargs))


def stage_enabled() -> bool:
    """URL-mode staging is available: bucket configured AND boto3 importable."""
    if not Settings.get_cams_stage_s3_bucket():
        return False
    try:
        _get_s3()
    except Exception:  # noqa: BLE001 — missing boto3 / broken creds chain
        logger.warning("CAMS_STAGE_S3_BUCKET is set but the S3 client is unavailable")
        return False
    return True


async def stage_pdf(data: bytes) -> tuple[str, str]:
    """Upload *data* under an unguessable key; return ``(presigned_url, key)``.

    The URL grants GET for ``_URL_TTL_SECONDS``; pass ``key`` to
    :func:`discard_staged_pdf` as soon as the parse call returns."""
    bucket = Settings.get_cams_stage_s3_bucket()
    key = f"{_KEY_PREFIX}{secrets.token_urlsafe(32)}.pdf"
    await _put_pdf(bucket, key, data)
    url = await asyncio.to_thread(
        s3.generate_presigned_url,
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=_URL_TTL_SECONDS,
    )
    return url, key


async def discard_staged_pdf(key: Optional[str]) -> None:
    """Best-effort delete; the bucket lifecycle rule is the backstop."""
    if not key:
        return
    try:
        await asyncio.to_thread(
            _get_s3().delete_object,
            Bucket=Settings.get_cams_stage_s3_bucket(),
            Key=key,
        )
    except Exception:  # noqa: BLE001
        logger.warning("could not delete staged CAS PDF from S3 (lifecycle will)")


# ── permanent per-user CAS archive (`user-cas/` prefix) ─────────────────────
# Unlike `cams-stage/`, these objects are KEPT — they back the "My CAS
# statements" list on the profile. Do NOT put a lifecycle expiry on this
# prefix. The PDFs remain protected by the user's own statement password on
# top of SSE at rest.

_ARCHIVE_PREFIX = "user-cas/"
_DOWNLOAD_TTL_SECONDS = 5 * 60

# Content-Disposition filename: keep it boring — alnum, dot, dash, underscore.
_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


async def archive_cas_pdf(user_id: Any, doc_id: Any, data: bytes) -> str:
    """Store a successfully parsed statement permanently; returns the S3 key."""
    bucket = Settings.get_cams_stage_s3_bucket()
    key = f"{_ARCHIVE_PREFIX}{user_id}/{doc_id}.pdf"
    await _put_pdf(bucket, key, data)
    return key


async def presign_cas_download(key: str, filename: Optional[str]) -> str:
    """Short-lived download URL for an archived statement (5 min)."""
    safe_name = _FILENAME_SAFE_RE.sub("_", filename or "") or "cas-statement.pdf"
    if not safe_name.lower().endswith(".pdf"):
        safe_name += ".pdf"
    s3 = _get_s3()
    return await asyncio.to_thread(
        s3.generate_presigned_url,
        "get_object",
        Params={
            "Bucket": Settings.get_cams_stage_s3_bucket(),
            "Key": key,
            "ResponseContentType": "application/pdf",
            "ResponseContentDisposition": f'attachment; filename="{safe_name}"',
        },
        ExpiresIn=_DOWNLOAD_TTL_SECONDS,
    )


async def delete_cas_object(key: str) -> None:
    """Delete an archived statement object (user removed it from their profile)."""
    await asyncio.to_thread(
        _get_s3().delete_object,
        Bucket=Settings.get_cams_stage_s3_bucket(),
        Key=key,
    )
