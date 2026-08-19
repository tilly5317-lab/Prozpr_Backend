"""Typed plan operations, and the arithmetic that produces them.

The extractor reads a message into the PARTS of a number — a bare figure, an
Indian magnitude word, a period, or a relative instruction — and this module
multiplies them out. That split is the whole safety story of the domain, and it
exists because of a real failure: asked to convert "2.4 lakh a month" into an
annual figure, Haiku returned 28,800,000 (a crore out) at 0.95 confidence, and
a digit-count slip is indistinguishable from a correct answer. A model cannot
make that mistake about arithmetic it is never asked to do.

Relative changes are the same principle taken one step further. "My income went
up 20%" cannot be resolved without the current income — so the model returns
the INSTRUCTION (increase, 20, percent) and this module reads the stored value
and computes the result. Two things fall out of that, both wanted:

  * the model never needs to be told what the customer earns (see ``privacy``);
  * a relative change with nothing on file is a QUESTION, not a guess —
    ``NoBaseline`` is raised and the caller asks for the absolute figure.

Nothing here touches the database or an LLM. Pure functions and dataclasses, so
the arithmetic can be tested without either.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.domains.profile.services.profile_field_registry import FieldSpec

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

# What the operation acts on.
Target = Literal["profile", "goal", "plan"]

# The verb. Read as CRUD:
#   create  -> goal only (a profile field is `set`, there is nothing to create)
#   read    -> profile field or goal list; never writes
#   set / adjust / update -> the U, absolute and relative respectively
#   clear / delete        -> the D; a profile field goes back to NULL, a goal row
#                            is removed and kept whole on the audit row
#   project -> run the plan; not CRUD, but it is what the customer asks for
#              immediately after any of the above
Verb = Literal["set", "adjust", "clear", "read", "create", "update", "delete", "project"]

# How a figure was scaled by the customer.
Magnitude = Literal["unit", "thousand", "lakh", "crore"]
Period = Literal["per_month", "per_year", "none"]

_MAGNITUDE: dict[str, float] = {
    "unit": 1.0,
    "thousand": 1_000.0,
    "lakh": 100_000.0,
    "crore": 10_000_000.0,
}

# Registry units that carry a period, and the period the column stores.
_STORED_PERIOD: dict[str, str] = {
    "inr_per_year": "per_year",
    "inr_per_month": "per_month",
}


class AmbiguousUnit(Exception):
    """A per-period field arrived with no period. Ask; never pick one."""


class NoBaseline(Exception):
    """A relative change with nothing stored to apply it to."""


# ---------------------------------------------------------------------------
# The operation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Operation:
    """One resolved thing to do to the customer's plan.

    ``value`` is already in the unit the column (or the goal slot) stores —
    every conversion has happened by the time an Operation exists, so a
    consumer never re-derives a figure and never has to know how it was said.
    """

    target: Target
    verb: Verb
    # Registry key for a profile operation.
    field_key: str | None = None
    # What the customer called the goal ("the car", "Europe trip"). Matched to a
    # row by ``goal_ops.resolve_ref``; never an id, because customers do not
    # have ids.
    goal_ref: str | None = None
    # Goal slots for create / update, in the goal builder's vocabulary.
    slots: dict[str, Any] | None = None
    value: Any = None
    confidence: float = 1.0
    verbatim: str | None = None
    # Set for a relative change: what it was applied to and how, so the reply
    # can show the customer the working rather than just the answer.
    basis: str | None = None
    source: str = "chat_answer"

    @property
    def writes(self) -> bool:
        return self.verb in ("set", "adjust", "clear", "create", "update", "delete")

    @property
    def is_profile(self) -> bool:
        return self.target == "profile"

    @property
    def is_goal(self) -> bool:
        return self.target == "goal"


# ---------------------------------------------------------------------------
# Absolute figures
# ---------------------------------------------------------------------------


def scale(amount: float, magnitude: str | None) -> float:
    """Apply the Indian magnitude word. The one multiplication a model may not do."""
    return float(amount) * _MAGNITUDE.get(magnitude or "unit", 1.0)


def to_stored_value(
    fs: FieldSpec,
    *,
    amount: float | None,
    magnitude: str | None,
    period: str | None,
    text_value: str | None,
) -> Any:
    """Turn the reported parts into the value the column stores.

    Raises ``AmbiguousUnit`` when a per-month/per-year field arrives with no
    period at all. Guessing costs every projection built on the figure;
    asking costs one turn.
    """
    if fs.input_kind in ("enum", "date", "text"):
        return (text_value or "").strip()

    if amount is None:
        return None

    value = scale(amount, magnitude)

    stored_period = _STORED_PERIOD.get(fs.unit)
    if stored_period is not None:
        said = period or "none"
        if said == "none":
            raise AmbiguousUnit(fs.key)
        if said == "per_month" and stored_period == "per_year":
            value *= 12
        elif said == "per_year" and stored_period == "per_month":
            value /= 12

    if fs.input_kind == "integer":
        return int(round(value))
    return round(value, 2)


# ---------------------------------------------------------------------------
# Relative changes — "my income went up 20%", "we spend 10k more a month"
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RelativeChange:
    """A change expressed against whatever is currently on file."""

    direction: Literal["increase", "decrease"]
    pct: float | None = None
    amount: float | None = None
    magnitude: str | None = None
    period: str | None = None

    @property
    def is_pct(self) -> bool:
        return self.pct is not None


def apply_relative(
    fs: FieldSpec,
    current: Any,
    change: RelativeChange,
) -> tuple[Any, str]:
    """Resolve a relative change against the stored value.

    Returns ``(new_value, basis)`` where ``basis`` describes the working in
    plain words — the customer said "up 20%", and being shown "20% on the
    ₹30,00,000 we had on file" is how they catch it if we applied it to the
    wrong number.

    Raises ``NoBaseline`` when nothing is stored: a percentage of an unknown
    figure is not a figure, and inventing a starting point would put a number
    in their plan that neither of us chose.
    """
    if current is None or (isinstance(current, str) and not current.strip()):
        raise NoBaseline(fs.key)
    try:
        base = float(current)
    except (TypeError, ValueError) as exc:
        # A percentage of an enum or a date is meaningless.
        raise NoBaseline(fs.key) from exc

    sign = 1.0 if change.direction == "increase" else -1.0

    if change.is_pct:
        pct = abs(float(change.pct or 0.0))
        new = base * (1.0 + sign * pct / 100.0)
        basis = f"{pct:g}% {change.direction} on the {_plain(fs, base)} on file"
    else:
        if change.amount is None:
            raise NoBaseline(fs.key)
        delta = scale(change.amount, change.magnitude)
        # A delta stated per month against a per-year column (and the reverse)
        # is converted exactly as an absolute figure would be.
        stored_period = _STORED_PERIOD.get(fs.unit)
        if stored_period is not None:
            said = change.period or "none"
            if said == "none":
                raise AmbiguousUnit(fs.key)
            if said == "per_month" and stored_period == "per_year":
                delta *= 12
            elif said == "per_year" and stored_period == "per_month":
                delta /= 12
        new = base + sign * delta
        basis = (
            f"{_plain(fs, delta)} {'more' if sign > 0 else 'less'} than the "
            f"{_plain(fs, base)} on file"
        )

    new = max(0.0, new)
    if fs.min_value is not None:
        new = max(float(fs.min_value), new)
    if fs.max_value is not None:
        new = min(float(fs.max_value), new)

    if fs.input_kind == "integer":
        return int(round(new)), basis
    return round(new, 2), basis


def _plain(fs: FieldSpec, value: float) -> str:
    """A figure in the words the reply will use. Money gets Indian grouping."""
    if fs.input_kind in ("money",):
        from app.domains.ai_engine.common import format_inr_indian

        return format_inr_indian(value) or f"{value:,.0f}"
    if fs.input_kind == "percent":
        return f"{value:g}%"
    return f"{value:g}"


__all__ = [
    "AmbiguousUnit",
    "Magnitude",
    "NoBaseline",
    "Operation",
    "Period",
    "RelativeChange",
    "Target",
    "Verb",
    "apply_relative",
    "scale",
    "to_stored_value",
]
