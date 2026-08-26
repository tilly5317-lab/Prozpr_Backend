"""Service — `avatar_service.py`.

Profile pictures in S3, under an ``avatars/`` prefix on the same bucket the CAS
archive uses. No new bucket and no new IAM: the instance role already writes
there, and one storage story is easier to reason about at audit time than two.

Two things this deliberately does NOT do:

* **Resize.** There is no Pillow in this service, and adding an image library to
  a financial backend to shrink a photograph is a poor trade — decoders are a
  classic memory-safety surface. The client downscales to 512px before it
  uploads; the size cap here is the backstop for a client that doesn't.
* **Trust Content-Type.** The header is caller-supplied. The magic bytes decide,
  so a PDF or an HTML file cannot arrive labelled ``image/png`` and later be
  served back from our own origin.

Objects are private. Reads go through a short-lived presigned URL, which is why
``/auth/me`` carries only ``avatar_set`` — a presigned URL on an endpoint that
is called on every page load would be minted constantly and cached nowhere.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from app.core.config import Settings

logger = logging.getLogger(__name__)

_PREFIX = "avatars/"
_URL_TTL_SECONDS = 60 * 60

#: 4 MB. The client sends a 512px re-encode that lands well under 200 KB, so
#: anything near this cap means the browser path was bypassed.
MAX_AVATAR_BYTES = 4 * 1024 * 1024

#: Magic bytes → the content type we will serve the object back as. Only these
#: three; SVG is excluded on purpose because it is a script-execution vector.
_SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
)

_s3_client: Any = None


def _get_s3() -> Any:
    global _s3_client
    if _s3_client is None:
        import boto3  # heavy optional dependency — keep the import lazy

        _s3_client = boto3.client("s3")  # region/creds from the default chain
    return _s3_client


class UnsupportedImage(ValueError):
    """The bytes are not a JPEG, PNG or WebP we are willing to store."""


def sniff_image(data: bytes) -> tuple[str, str]:
    """Return ``(content_type, extension)`` from the magic bytes.

    Raises ``UnsupportedImage`` rather than guessing. WebP needs a two-part
    check: the RIFF container is shared with other formats, so the ``WEBP``
    marker at offset 8 is what actually identifies it."""
    for prefix, content_type, ext in _SIGNATURES:
        if data.startswith(prefix):
            return content_type, ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    raise UnsupportedImage("Only JPEG, PNG and WebP images are supported")


async def put_avatar(user_id: Any, data: bytes) -> str:
    """Store the image and return its S3 key. Overwrites any previous one for
    this user — an avatar has no history worth keeping, and a key that includes
    the extension keeps the served content type honest."""
    content_type, ext = sniff_image(data)
    bucket = Settings.get_cams_stage_s3_bucket()
    key = f"{_PREFIX}{user_id}/avatar.{ext}"

    put_kwargs: dict[str, Any] = {
        "Bucket": bucket,
        "Key": key,
        "Body": data,
        "ContentType": content_type,
        # The bucket is private; this is belt and braces against a future
        # bucket-policy change making the prefix world-readable.
        "CacheControl": "private, max-age=3600",
    }
    kms_key = Settings.get_cams_stage_kms_key_id()
    if kms_key:
        put_kwargs["ServerSideEncryption"] = "aws:kms"
        put_kwargs["SSEKMSKeyId"] = kms_key
    else:
        put_kwargs["ServerSideEncryption"] = "AES256"

    await asyncio.to_thread(lambda: _get_s3().put_object(**put_kwargs))
    return key


async def presign_avatar(key: str) -> str:
    """A one-hour read URL. Presigning is local signing, not an S3 round trip,
    so this is cheap enough to mint per request."""
    return await asyncio.to_thread(
        _get_s3().generate_presigned_url,
        "get_object",
        Params={"Bucket": Settings.get_cams_stage_s3_bucket(), "Key": key},
        ExpiresIn=_URL_TTL_SECONDS,
    )


async def delete_avatar(key: Optional[str]) -> None:
    """Remove the object. Missing keys are not an error — the caller's intent is
    "there should be no avatar", and S3 delete is already idempotent."""
    if not key:
        return
    try:
        await asyncio.to_thread(
            _get_s3().delete_object,
            Bucket=Settings.get_cams_stage_s3_bucket(),
            Key=key,
        )
    except Exception:  # noqa: BLE001 — clearing the column matters more
        logger.warning("Could not delete avatar object %s", key, exc_info=True)
