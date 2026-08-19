"""What may leave the database and reach a model, and in what shape.

This domain reads a customer's income, expenses, savings, date of birth and
goals, and writes them back. That makes it the one place in chat where getting
the privacy boundary wrong is expensive, so the boundary is stated here rather
than implied by each call site.

Two rules, and they are enforced by construction, not by prompt wording:

  1. **The extractor never sees a stored value.** It is told which fields
     EXIST and what units they are in — never what the customer's income is,
     what they have saved, or when they were born. It cannot leak a figure it
     was never given. This is also why a relative change ("up 20%") is
     returned as an INSTRUCTION and multiplied out in
     ``operations.resolve`` against the value read from the database: the
     arithmetic needs the current figure, and Python is where the current
     figure lives.

  2. **The formatter sees only what the reply must contain.** Reading a value
     back ("your income is on file as ₹32L") means putting that value in a
     prompt — unavoidable, and consented to by the customer asking. So the
     facts pack carries the fields under discussion and nothing else: never the
     whole snapshot, never an identifier, never a field the turn is not about.

Everything else here is scrubbing. Free text — the customer's own message and
the recent history — can contain identifiers they typed for their own reasons
(a PAN while asking about tax, a phone number in an aside). Those are stripped
before the text reaches a model and before it is stored as ``verbatim`` on the
audit row, because the audit row is read by humans.
"""

from __future__ import annotations

import re
from typing import Any

# Identifiers that must never reach a model or an audit row. Ordered
# most-specific first: PAN before the generic long-digit rule, so a PAN is
# labelled as one rather than partially masked.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # PAN: five letters, four digits, one letter.
    ("[pan]", re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", re.IGNORECASE)),
    # Aadhaar: 12 digits, optionally grouped 4-4-4.
    ("[aadhaar]", re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b")),
    ("[email]", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    # Indian mobile: optional +91 / 0 prefix, then a 10-digit number starting 6-9.
    ("[phone]", re.compile(r"(?<![\d,.])(?:\+?91[ -]?|0)?[6-9]\d{9}(?![\d,.])")),
    # Bank / demat / folio: 11+ unbroken digits. Deliberately ABOVE any plausible
    # rupee figure written without separators (₹10,00,00,000 is 8 digits), so a
    # customer stating a crore is not mistaken for an account number.
    ("[account]", re.compile(r"(?<![\d,.])\d{11,}(?![\d,.])")),
)


def redact(text: str | None) -> str:
    """Strip identifiers from free text before it reaches a model or an audit row.

    Deliberately conservative about money: Indian rupee figures are written
    with commas or magnitude words, and the numeric patterns above are anchored
    so a bare ``2880000`` survives. Losing an amount here would be worse than
    the identifier it was protecting — the extractor would silently read a
    different number.
    """
    if not text:
        return ""
    out = str(text)
    for label, pattern in _PATTERNS:
        out = pattern.sub(label, out)
    return out


def redact_history(
    history: list[dict[str, Any]] | None,
    *,
    max_turns: int,
    max_chars: int = 400,
) -> list[dict[str, str]]:
    """The last ``max_turns`` messages, scrubbed and truncated.

    History is capped as much for privacy as for tokens: the further back a
    turn is, the less it says about THIS message and the more unrelated
    personal detail it carries.
    """
    if not history:
        return []
    out: list[dict[str, str]] = []
    for msg in history[-max_turns:]:
        out.append(
            {
                "role": str(msg.get("role", "")) or "user",
                "content": redact(str(msg.get("content", "")))[:max_chars],
            }
        )
    return out


def verbatim_for_audit(text: str | None, *, limit: int = 300) -> str | None:
    """The customer's own words, safe to store beside a value on the audit row."""
    cleaned = redact(text).strip()
    return cleaned[:limit] or None


__all__ = ["redact", "redact_history", "verbatim_for_audit"]
