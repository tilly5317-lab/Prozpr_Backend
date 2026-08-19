"""Per-turn counterfactual overrides for the goal-planning engine.

A leaf module, mirroring ``aa_engine/overrides.py``: neither ``chat.py`` nor
``service.py`` imports the other, but both import from here.

The cashflow projection is pure Python and cheap to re-run, so "what if I retire
at 50?" is answered by rebuilding the input with one field changed and running
the engine again — never by asking the formatter to reason about numbers it was
never given.

Keys are whitelisted because the caller is an LLM detector. An unrecognised key
must fail loudly: silently modelling something the customer did not ask for
would produce a confident, wrong projection, which is worse than an error.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Four to start. Each maps to a question customers actually ask; the assumption
# knobs (return rates, income growth) are deliberately NOT here — letting a
# customer shop for a more optimistic ROI until the plan "works" is a different
# product decision, not a formatting one.
ALLOWED_OVERRIDE_KEYS = frozenset(
    {
        "retirement_age",  # "what if I retire at 50?"
        "starting_monthly_investment",  # "what if I invest ₹50k more a month?"
        "monthly_household_expense",  # "what if my expenses go to ₹3L?"
        "one_off_outflow",  # "can I afford a ₹10L trip next year?"
    }
)

_RETIREMENT_KEYS = {"retirement_age"}
_PROFILE_KEYS = {"starting_monthly_investment", "monthly_household_expense"}


def apply_overrides(gp_input: Any, overrides: dict[str, Any] | None) -> Any:
    """Return a copy of ``gp_input`` with the overrides applied.

    The original is never mutated — a counterfactual must not leak into the
    customer's real plan. Returns the input unchanged (same object) when there
    is nothing to apply, so the caller can cheaply tell base from counterfactual.

    Raises ``ValueError`` on an unknown key.
    """
    if not overrides:
        return gp_input

    unknown = set(overrides) - ALLOWED_OVERRIDE_KEYS
    if unknown:
        raise ValueError(f"unknown override key(s): {sorted(unknown)}")

    updates: dict[str, Any] = {}

    retirement_updates = {
        k: v for k, v in overrides.items() if k in _RETIREMENT_KEYS
    }
    if retirement_updates:
        updates["retirement"] = gp_input.retirement.model_copy(
            update=retirement_updates
        )

    profile_updates = {k: v for k, v in overrides.items() if k in _PROFILE_KEYS}
    if profile_updates:
        updates["profile"] = gp_input.profile.model_copy(update=profile_updates)

    if "one_off_outflow" in overrides:
        events = _coerce_one_off_events(overrides["one_off_outflow"])
        if events:
            # APPEND, never replace: the customer's existing planned outflows are
            # part of the plan they are asking about.
            updates["one_off_outflows"] = [*gp_input.one_off_outflows, *events]

    return gp_input.model_copy(update=updates)


def _coerce_one_off_events(raw: Any) -> list[Any]:
    """Turn whatever the model put in ``one_off_outflow`` into real events.

    ``overrides`` is a free-form dict on an LLM structured output, so its shape
    is a suggestion rather than a contract. Asked about two one-off expenses in
    one sentence the model returned a LIST where the code assumed a single
    mapping, and ``OneOffEvent(**event)`` raised — taking down the whole turn
    with "I couldn't process your request due to a technical issue".

    A counterfactual is an enhancement to the answer, never the answer itself,
    so anything unusable here is dropped and logged rather than raised. The
    projection still runs; it just runs without the hypothetical.
    """
    from cashflow_statement.models import OneOffEvent

    items = raw if isinstance(raw, (list, tuple)) else [raw]
    out: list[Any] = []
    for item in items:
        if isinstance(item, OneOffEvent):
            out.append(item)
            continue
        if isinstance(item, dict):
            try:
                out.append(OneOffEvent(**item))
            except Exception:
                logger.warning(
                    "dropping unusable one_off_outflow override %r", item
                )
            continue
        logger.warning(
            "ignoring one_off_outflow override of type %s", type(item).__name__
        )
    return out
