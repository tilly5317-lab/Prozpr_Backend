"""Deterministic normalization for customer investment preferences.

Spec: docs/superpowers/specs/2026-08-24-investment-preferences-design.md.
Scope ("only equity") normalizes into absolute tilts so there is ONE
pre-engine mechanism. Magnitude defaults are policy, never LLM judgment.
Pure functions — no I/O, no LLM.
"""

from __future__ import annotations

from dataclasses import dataclass

ASSET_CLASSES = ("equity", "debt", "others")

# No-number tilt ("more equity", "make it safer") → move the named class this
# many percentage points from the RECOMMENDED mix. A relative step guarantees
# "more" always means more (a risk-band anchor could land below the recommended
# plan for an already-aggressive profile and read as a step backward).
DEFAULT_TILT_STEP_PP = 10.0


@dataclass(frozen=True)
class TiltResult:
    mix_pct: dict[str, float] | None
    default_step_applied: bool = False


def _renormalized(mix: dict[str, float], pinned: dict[str, float]) -> dict[str, float]:
    """Hold ``pinned`` classes fixed; scale the rest pro-rata to sum 100."""
    free = [c for c in ASSET_CLASSES if c not in pinned]
    pinned_total = sum(pinned.values())
    free_current = sum(mix[c] for c in free)
    remaining = max(0.0, 100.0 - pinned_total)
    out = dict(pinned)
    for c in free:
        out[c] = (
            remaining * mix[c] / free_current
            if free_current > 0
            else (remaining / len(free) if free else 0.0)
        )
    return out


def normalize_tilt(
    current_mix_pct: dict[str, float],
    *,
    scope_only: list[str] | None,
    tilt_asset_class: str | None,
    tilt_delta_pp: float | None,
    tilt_target_pct: float | None,
) -> TiltResult:
    """Resolve scope + tilt into one absolute target mix.

    Baseline is the RECOMMENDED target mix (never current holdings) — the caller
    passes it in. A tilt with no number applies ``DEFAULT_TILT_STEP_PP`` toward
    the named class (``default_step_applied=True`` so the caller discloses it).
    """
    mix = {c: float(current_mix_pct.get(c, 0.0)) for c in ASSET_CLASSES}

    if scope_only:
        allowed = {c for c in scope_only if c in ASSET_CLASSES}
        pinned = {c: 0.0 for c in ASSET_CLASSES if c not in allowed}
        return TiltResult(mix_pct=_renormalized(mix, pinned))

    if tilt_asset_class is None:
        return TiltResult(mix_pct=None)

    default_step = tilt_delta_pp is None and tilt_target_pct is None
    if default_step:
        tilt_delta_pp = DEFAULT_TILT_STEP_PP
    target = (
        tilt_target_pct
        if tilt_target_pct is not None
        else mix[tilt_asset_class] + tilt_delta_pp
    )
    clamped = min(100.0, max(0.0, target))
    return TiltResult(
        mix_pct=_renormalized(mix, {tilt_asset_class: clamped}),
        default_step_applied=default_step,
    )
