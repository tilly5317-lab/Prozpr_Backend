"""Readiness check for the rebalancing engine.

Mirrors the cashflow / goal-planning readiness gate. Reports which user-supplied
inputs the rebalancing module needs and whether each is present — WITHOUT
substituting defaults. The engine refuses to run until the user has a date of
birth and at least one mutual-fund holding (see
``rebal_engine.service.compute_rebalancing_result``), so trades are never planned
on placeholder data.

This module is the single source of truth for "what does rebalancing need from
the user". The ``GET /rebalancing/readiness`` endpoint serialises it and the
frontend gate renders the unlock form straight from this list, so the questions
stay consistent between the engine and the UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    group: str
    kind: str  # "date" | "money" | "int" | "percent"
    unit: Optional[str]
    getter: Callable[[Any], Any]
    help: Optional[str] = None
    # Optional fields are surfaced in the form but do NOT block the engine.
    optional: bool = False


# Order here drives the order the fields appear in the unlock form.
REQUIRED_REBALANCING_FIELDS: List[FieldSpec] = [
    FieldSpec(
        "date_of_birth",
        "Date of birth",
        "About you",
        "date",
        None,
        lambda u: getattr(u, "date_of_birth", None),
        help="Anchors tax aging (STCG vs LTCG) and your risk profile when planning trades.",
    ),
]


# Pseudo-requirement: at least one MF holding must exist. It is NOT a form field
# (holdings can't be typed in) — the frontend renders a "Connect your portfolio"
# action for it instead. Surfaced in ``missing`` so the gate knows to show it.
HOLDINGS_KEY = "mf_holdings"


def _serialize_value(spec: FieldSpec, raw: Any) -> Optional[Any]:
    if raw is None:
        return None
    if spec.kind == "date":
        return raw.isoformat() if hasattr(raw, "isoformat") else str(raw)
    if spec.kind == "percent":
        return round(float(raw) * 100, 4)
    if spec.kind == "int":
        return int(raw)
    return float(raw)


def evaluate_rebalancing_readiness(user: Any, *, has_holdings: bool) -> Dict[str, Any]:
    """Return ``{ready, missing, fields, has_holdings}`` for rebalancing inputs.

    ``has_holdings`` is supplied by the caller (a cheap DB existence check) since
    holdings live in another domain and aren't a user attribute.
    """
    fields: List[Dict[str, Any]] = []
    missing: List[str] = []
    for spec in REQUIRED_REBALANCING_FIELDS:
        raw = spec.getter(user)
        present = raw is not None
        if not present and not spec.optional:
            missing.append(spec.key)
        fields.append(
            {
                "key": spec.key,
                "label": spec.label,
                "group": spec.group,
                "kind": spec.kind,
                "unit": spec.unit,
                "help": spec.help,
                "optional": spec.optional,
                "present": present,
                "value": _serialize_value(spec, raw),
            }
        )
    if not has_holdings:
        missing.append(HOLDINGS_KEY)
    return {
        "ready": len(missing) == 0,
        "missing": missing,
        "fields": fields,
        "has_holdings": has_holdings,
    }
