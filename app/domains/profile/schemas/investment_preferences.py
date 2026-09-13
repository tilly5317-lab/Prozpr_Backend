"""Pydantic schemas for the saved investment-preferences API (spec §4.4)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

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


# ---------------------------------------------------------------------------
# S4 percentage screen (spec 2026-09-10-investment-preferences-s4-pct-screen)
# ---------------------------------------------------------------------------


class ScreenPin(BaseModel):
    """A subcategory pinned to an exact share of the WHOLE portfolio."""

    subgroup: str
    pct_of_total: float


class ScreenPreferenceRequest(BaseModel):
    """The redesigned screen's save payload: an explicit three-class split
    (% of total, sums to 100) plus optional subcategory pins (% of total)."""

    class_mix: dict[str, float]
    pins: list[ScreenPin] = []


class ScreenSubcategory(BaseModel):
    """One settable subcategory: id, class, display label, and Prozpr's
    recommended share of total — feeds both the grouped dropdown and each
    pin's "Prozpr N%"."""

    id: str
    class_: str = Field(alias="class", serialization_alias="class")
    label: str
    recommended_pct_of_total: float

    model_config = {"populate_by_name": True}


class ScreenSaved(BaseModel):
    class_mix: dict[str, float]
    pins: list[ScreenPin]
    saved_at: Optional[datetime] = None


class ScreenPreferenceGetResponse(BaseModel):
    """GET payload: the customer's saved split (or null), Prozpr's class-level
    recommendation, and the settable-subcategory list."""

    saved: Optional[ScreenSaved] = None
    recommendation: dict[str, dict[str, float]]
    subcategories: list[ScreenSubcategory]


class ScreenSaveResponse(BaseModel):
    ok: bool = True
    saved_at: Optional[datetime] = None
    blocked: Optional[str] = None
    no_op: bool = False
