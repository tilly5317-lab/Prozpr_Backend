"""Personal-data primitives: masking for display, redaction for anything exported.

Two different jobs, deliberately kept apart:

* ``mask_*`` — turn ONE known field into a partial value a human can still
  recognise (``*****3210``). Use these when a response or a log line legitimately
  needs to reference an identifier the caller already knows.
* ``redact_text`` / ``redact_obj`` — sweep an ARBITRARY blob for anything that
  looks like an identifier and replace it. Use these on text we did not compose:
  third-party error bodies, exception messages, log records.

Redaction is a backstop, never a licence to hand PII to a sink. Fix the call
site first; this exists for the strings nobody thought about.

The patterns stay narrow on purpose. Broad ones eat order ids, ISINs and rupee
amounts, which makes an incident unreadable and pushes people to turn scrubbing
off — worse than the leak it prevented.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "mask_pan",
    "mask_email",
    "mask_mobile",
    "mask_account",
    "mask_name",
    "redact_text",
    "redact_obj",
    "PII_PATTERNS",
    "REDACTED",
]

REDACTED = "[REDACTED]"

# ── field-level masks ──────────────────────────────────────────────────────


def mask_pan(pan: str | None) -> str | None:
    """``ABCDE1234F`` -> ``XXXXXX234F``.

    Keeps the last four so a user can confirm which PAN we hold, drops the five
    leading letters (surname initial + holder type) and the first digit, which
    is what makes a PAN guessable from a name.
    """
    if not pan:
        return pan
    v = pan.strip()
    return "X" * max(len(v) - 4, 0) + v[-4:] if len(v) > 4 else "X" * len(v)


def mask_email(email: str | None) -> str | None:
    """``priya.sharma@gmail.com`` -> ``pr***@gmail.com``."""
    if not email or "@" not in email:
        return email
    local, _, domain = email.partition("@")
    keep = local[:2] if len(local) > 2 else local[:1]
    return f"{keep}***@{domain}"


def mask_mobile(number: str | None) -> str | None:
    """Last four digits only. Matches the shape OTP logs already used."""
    if not number:
        return number
    digits = "".join(c for c in number if c.isdigit())
    return f"*****{digits[-4:]}" if len(digits) >= 4 else "*****"


def mask_account(number: str | None) -> str | None:
    """Bank / demat / folio identifier -> ``****1234``."""
    if not number:
        return number
    v = number.strip()
    return f"****{v[-4:]}" if len(v) >= 4 else "****"


def mask_name(name: str | None) -> str | None:
    """``Priya Sharma`` -> ``P. S.`` — enough to match a record, not to identify."""
    if not name:
        return name
    parts = [p for p in name.strip().split() if p]
    return " ".join(f"{p[0].upper()}." for p in parts) if parts else name


# ── blob-level redaction ───────────────────────────────────────────────────

# Ordered longest-match-first where two could overlap: an email contains no
# PAN, but a 12-digit Aadhaar would otherwise be eaten by the 10-digit mobile
# pattern's tail, so Aadhaar is matched first.
PII_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Email. Runs first — its local part can look like other identifiers.
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b"),
    # PAN: 5 letters, 4 digits, 1 letter. Case-insensitive because the FP client
    # lower-cases PAN before sending it, which an upper-only pattern misses —
    # that is the exact shape that reached error tracking.
    re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b", re.IGNORECASE),
    # Aadhaar: 12 digits, optionally spaced 4-4-4. Must precede the mobile rule.
    re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b"),
    # IFSC: 4 letters, 0, 6 alphanumerics.
    re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b", re.IGNORECASE),
    # Indian mobile, with or without the country code.
    re.compile(r"\b(?:\+?91[\-\s]?)?[6-9]\d{9}\b"),
)

# Keys whose VALUE is redacted wholesale, regardless of shape — the value may be
# a name, an address or a date of birth, none of which have a safe regex.
_SENSITIVE_KEYS = frozenset(
    {
        "pan",
        "pan_no",
        "pan_number",
        "aadhaar",
        "aadhaar_number",
        "taxid_number",
        "email",
        "email_address",
        "mobile",
        "phone",
        "phone_number",
        "contact_number",
        "date_of_birth",
        "dob",
        "birth_date",
        "address",
        "address_line_1",
        "address_line_2",
        "address_line_3",
        "pincode",
        "postal_code",
        "bank_account",
        "bank_account_number",
        "account_number",
        "ifsc",
        "ifsc_code",
        "folio",
        "folio_number",
        "name",
        "first_name",
        "middle_name",
        "last_name",
        "holder_name",
        "investor_name",
        "gender",
        "password",
        "pekrn",
    }
)

_MAX_DEPTH = 6


def redact_text(text: str) -> str:
    """Replace every identifier-shaped run in ``text``."""
    if not text:
        return text
    for pattern in PII_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def redact_obj(obj: Any, _depth: int = 0) -> Any:
    """Recursively redact a decoded JSON-ish structure.

    Handles the two ways personal data hides in a third-party payload: in a
    string that merely *looks* like an identifier, and under a key whose name
    tells us it is one even when the value is unremarkable (``"name": "Priya"``).

    Depth-capped and total — a redactor that raises turns a handled error into
    an unhandled one, so unknown types fall through to ``repr`` scrubbing.
    """
    if _depth > _MAX_DEPTH:
        return REDACTED
    try:
        if obj is None or isinstance(obj, (bool, int, float)):
            return obj
        if isinstance(obj, str):
            return redact_text(obj)
        if isinstance(obj, dict):
            return {
                k: (
                    REDACTED
                    if isinstance(k, str) and k.strip().lower() in _SENSITIVE_KEYS
                    else redact_obj(v, _depth + 1)
                )
                for k, v in obj.items()
            }
        if isinstance(obj, (list, tuple, set)):
            return [redact_obj(v, _depth + 1) for v in obj]
        return redact_text(repr(obj))
    except Exception:  # pragma: no cover - redaction must never raise
        return REDACTED
