"""Validated field value → the table that owns it.

The single cross-table writer for captured profile answers. It dispatches on
``FieldSpec.table``, creates the owning row when the customer has none yet, and
returns the previous value so the caller can write an undo-able audit row.

Commit-free by design: the chat router owns the transaction, so a turn that
fails afterwards rolls the profile write back with it. Never call
``db.commit()`` here.

Validation lives here rather than in the extractor because the extractor is not
the only writer: ``/profile/complete`` and the onboarding forms come through the
same door, and a rule enforced in one path only is a rule the other path
breaks.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity.models.user import User
from app.domains.profile.models.investment_profile import InvestmentProfile
from app.domains.profile.models.personal_finance_profile import PersonalFinanceProfile
from app.domains.profile.models.risk_profile import RiskProfile
from app.domains.profile.models.tax_profile import TaxProfile
from app.domains.profile.services.profile_field_registry import FieldSpec, spec

logger = logging.getLogger(__name__)


class FieldValidationError(ValueError):
    """The supplied value is not usable for this field."""


@dataclass(frozen=True)
class WriteResult:
    key: str
    previous: Any
    value: Any
    table: str
    column: str
    risk_input: bool


# ---------------------------------------------------------------------------
# Validation / coercion
# ---------------------------------------------------------------------------

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y")


def _coerce_number(raw: Any, fs: FieldSpec) -> float:
    if isinstance(raw, bool):
        raise FieldValidationError(f"{fs.key}: expected a number")
    try:
        value = float(str(raw).replace(",", "").replace("₹", "").strip())
    except (TypeError, ValueError) as exc:
        raise FieldValidationError(f"{fs.key}: {raw!r} is not a number") from exc
    if fs.min_value is not None and value < fs.min_value:
        raise FieldValidationError(f"{fs.key}: {value} is below {fs.min_value}")
    if fs.max_value is not None and value > fs.max_value:
        raise FieldValidationError(f"{fs.key}: {value} is above {fs.max_value}")
    return value


def _coerce_enum(raw: Any, fs: FieldSpec) -> str:
    text = str(raw).strip()
    if not text:
        raise FieldValidationError(f"{fs.key}: empty answer")
    for option in fs.options:
        if text == option:
            return option
    lowered = text.casefold()
    for option in fs.options:
        if lowered == option.casefold():
            return option
    # A short answer that uniquely prefixes exactly one option ("old", "new",
    # "5+ years") — the extractor is asked for verbatim options, but the widget
    # and a terse human answer both land here.
    prefixed = [o for o in fs.options if o.casefold().startswith(lowered)]
    if len(prefixed) == 1:
        return prefixed[0]
    contained = [o for o in fs.options if lowered in o.casefold()]
    if len(contained) == 1:
        return contained[0]
    raise FieldValidationError(
        f"{fs.key}: {text!r} does not match any allowed option"
    )


def _coerce_date(raw: Any) -> date:
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    text = str(raw).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise FieldValidationError(f"date_of_birth: {text!r} is not a recognisable date")


def validate_value(key: str, raw: Any) -> Any:
    """Coerce ``raw`` into the type/units the column stores. Raises on junk."""
    fs = spec(key)
    if fs is None:
        raise FieldValidationError(f"{key}: not a registry field")
    if raw is None:
        raise FieldValidationError(f"{key}: no value")

    if fs.input_kind in ("money", "percent"):
        return _coerce_number(raw, fs)
    if fs.input_kind == "integer":
        return int(_coerce_number(raw, fs))
    if fs.input_kind == "enum":
        return _coerce_enum(raw, fs)
    if fs.input_kind == "date":
        parsed = _coerce_date(raw)
        today = date.today()
        age = today.year - parsed.year
        if not (16 <= age <= 100):
            raise FieldValidationError(
                f"date_of_birth: {parsed.isoformat()} implies an implausible age"
            )
        return parsed
    text = str(raw).strip()
    if not text:
        raise FieldValidationError(f"{key}: empty answer")
    return text[:100]


# ---------------------------------------------------------------------------
# Row resolution — one loader per registry table
# ---------------------------------------------------------------------------


async def _row_for(db: AsyncSession, table: str, user_id: uuid.UUID) -> Any:
    """The user's row in ``table``, created (unflushed) when absent.

    ``users`` is the exception: it always exists, and a missing one is a bug
    worth surfacing rather than papering over.
    """
    if table == "users":
        row = (
            await db.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if row is None:
            raise FieldValidationError(f"no user row for {user_id}")
        return row

    model = {
        "personal_finance_profiles": PersonalFinanceProfile,
        "investment_profiles": InvestmentProfile,
        "risk_profiles": RiskProfile,
        "tax_profiles": TaxProfile,
    }.get(table)
    if model is None:
        raise FieldValidationError(f"registry table {table!r} has no writer")

    row = (
        await db.execute(select(model).where(model.user_id == user_id))
    ).scalar_one_or_none()
    if row is None:
        row = model(user_id=user_id)
        db.add(row)
    return row


async def apply_field(
    db: AsyncSession,
    user_id: uuid.UUID,
    key: str,
    raw_value: Any,
) -> WriteResult:
    """Validate and write one field. Returns the previous value for the audit row."""
    fs = spec(key)
    if fs is None:
        raise FieldValidationError(f"{key}: not a registry field")

    value = validate_value(key, raw_value)
    row = await _row_for(db, fs.table, user_id)
    previous = getattr(row, fs.column, None)
    setattr(row, fs.column, value)
    await db.flush()

    logger.info(
        "financial_planning wrote %s.%s for user=%s (was %r)",
        fs.table,
        fs.column,
        user_id,
        previous,
    )
    return WriteResult(
        key=key,
        previous=previous,
        value=value,
        table=fs.table,
        column=fs.column,
        risk_input=fs.risk_input,
    )


async def restore_field(
    db: AsyncSession,
    user_id: uuid.UUID,
    key: str,
    previous: Any,
) -> None:
    """Put a field back to a recorded previous value — including back to NULL.

    Deliberately bypasses ``validate_value``: the value being restored was
    already in the column, and NULL (the common undo target) would fail
    validation.
    """
    fs = spec(key)
    if fs is None:
        raise FieldValidationError(f"{key}: not a registry field")
    row = await _row_for(db, fs.table, user_id)
    if previous is not None and fs.input_kind == "date" and isinstance(previous, str):
        previous = _coerce_date(previous)
    setattr(row, fs.column, previous)
    await db.flush()


__all__ = [
    "FieldValidationError",
    "WriteResult",
    "apply_field",
    "restore_field",
    "validate_value",
]
