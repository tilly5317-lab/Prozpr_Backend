"""Pydantic schemas for the saved investment-preferences API (spec §4.4)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, field_validator

_ASSET_CLASSES = {"equity", "debt", "others"}
_DIRECTIONS = {"more", "heavy", "less", "none", "target"}
_SUBGROUP_TOKENS = {"more", "heavy", "less", "none"}


class InvestmentPreferenceIntent(BaseModel):
    """The qualitative payload the customer speaks/taps (screen or chat).

    ``subgroups`` is the single subgroup facet: each entry is a 5-state
    token — "more" | "heavy" | "less" | "none" (neutral = absent) — or a
    bare number for an explicit class-share target (chat). Market-cap
    language is display naming over the beta subgroups. Malformed payloads
    are rejected here (HTTP 422) rather than crashing mid-resolution.
    """

    asset_class: Optional[dict[str, Any]] = None
    subgroups: Optional[dict[str, Any]] = None
    confirm: bool = False

    @field_validator("asset_class")
    @classmethod
    def _check_asset_class(cls, v):
        if v is None:
            return v
        if v.get("class") not in _ASSET_CLASSES:
            raise ValueError(f"asset_class.class must be one of {sorted(_ASSET_CLASSES)}")
        direction = v.get("direction")
        if direction is not None and direction not in _DIRECTIONS:
            raise ValueError(f"asset_class.direction must be one of {sorted(_DIRECTIONS)}")
        if direction == "target":
            tp = v.get("target_pct")
            if not isinstance(tp, (int, float)) or isinstance(tp, bool) or not 0 <= tp <= 100:
                raise ValueError("asset_class.target_pct must be a number in 0..100")
        return v

    @field_validator("subgroups")
    @classmethod
    def _check_subgroups(cls, v):
        if v is None:
            return v
        for sg, token in v.items():
            if isinstance(token, bool):
                raise ValueError(f"subgroups[{sg}] must be a token or number, not a bool")
            if isinstance(token, (int, float)):
                if not 0 <= token <= 100:
                    raise ValueError(f"subgroups[{sg}] number must be in 0..100")
            elif token not in _SUBGROUP_TOKENS:
                raise ValueError(
                    f"subgroups[{sg}] must be a number or one of {sorted(_SUBGROUP_TOKENS)}"
                )
        return v


class InvestmentPreferencePreviewResponse(BaseModel):
    """Result of ``preview_or_save`` — a preview (confirm=False) or the
    post-confirm summary. ``no_op`` marks the anti-ratchet short-circuit or a
    clear on an already-empty preference."""

    recommendation: Optional[dict[str, float]] = None
    preferred: Optional[dict[str, float]] = None
    deviation: Optional[dict[str, float]] = None
    shortfall: Optional[str] = None
    no_op: bool = False


class InvestmentPreferenceResponse(BaseModel):
    """The active ``SavedInvestmentPreference`` row plus the GET-only
    neutral ``recommendation`` block. Class mixes are assembled as dicts
    from the row's flat pct columns; market-cap asks and exclusions (value
    0) appear inside ``resolved_targets`` (and, as the customer's words, in
    ``customer_choices``)."""

    asset_class_requested: Optional[dict[str, float]] = None
    asset_class_target: Optional[dict[str, float]] = None
    resolved_targets: Optional[dict[str, float]] = None
    customer_choices: Optional[dict[str, Any]] = None
    saved_at: Optional[datetime] = None
    recommendation: Optional[dict[str, float]] = None

    @classmethod
    def from_row(cls, row, *, recommendation: Optional[dict] = None):
        if row is None:
            return cls(recommendation=recommendation)
        return cls(
            asset_class_requested=row.asset_class_requested,
            asset_class_target=row.asset_class_target,
            resolved_targets=row.resolved_targets,
            customer_choices=row.customer_choices,
            saved_at=row.created_at,
            recommendation=recommendation,
        )
