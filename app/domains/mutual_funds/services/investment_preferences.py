"""Deterministic normalization for customer investment preferences.

Spec: docs/superpowers/specs/2026-08-24-investment-preferences-design.md.
Scope ("only equity") normalizes into absolute tilts so there is ONE
pre-engine mechanism. Magnitude defaults are policy, never LLM judgment.
Pure functions — no I/O, no LLM.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domains.ai_engine.common import ensure_ai_agents_path

ensure_ai_agents_path()

from common import RISK_CATEGORIES, category_for_effective_risk_score  # noqa: E402

ASSET_CLASSES = ("equity", "debt", "others")

# Upper score bound per band, aligned with common.category_for_effective_risk_score
# thresholds (2.125 / 4.375 / 6.625 / 8.875, scores span 1.0-10.0). Edges sit
# just below the threshold so the edge score still maps into the SAME band.
_BAND_UPPER = dict(zip(RISK_CATEGORIES, (2.125, 4.375, 6.625, 8.875, 10.0)))
_EDGE_EPS = 0.01


@dataclass(frozen=True)
class TiltResult:
    mix_pct: dict[str, float] | None
    needs_band_edge_default: bool = False


def band_edge_score(category: str) -> float:
    """Top effective-risk score that still maps into ``category``.

    ``_BAND_UPPER`` deliberately duplicates common.py's thresholds; this check
    is the sync mechanism — a real exception (not ``assert``, which ``-O``
    strips) so drift fails loudly in production, not silently in advice.
    """
    upper = _BAND_UPPER[category]
    edge = upper if upper == 10.0 else upper - _EDGE_EPS
    if category_for_effective_risk_score(edge) != category:
        raise ValueError(
            f"band table drift: edge {edge} no longer maps into {category!r}"
        )
    return edge


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
    """Resolve scope + tilt into one absolute target mix (or a default request).

    Baseline is the RECOMMENDED target mix (never current holdings) — the
    caller passes it in. Returns ``needs_band_edge_default=True`` when a tilt
    names a class but no number: the caller resolves it via band_edge_score
    (needs the customer's risk category, which lives caller-side).
    """
    mix = {c: float(current_mix_pct.get(c, 0.0)) for c in ASSET_CLASSES}

    if scope_only:
        allowed = {c for c in scope_only if c in ASSET_CLASSES}
        pinned = {c: 0.0 for c in ASSET_CLASSES if c not in allowed}
        return TiltResult(mix_pct=_renormalized(mix, pinned))

    if tilt_asset_class is None:
        return TiltResult(mix_pct=None)

    if tilt_delta_pp is None and tilt_target_pct is None:
        return TiltResult(mix_pct=None, needs_band_edge_default=True)

    target = (
        tilt_target_pct
        if tilt_target_pct is not None
        else mix[tilt_asset_class] + tilt_delta_pp
    )
    clamped = min(100.0, max(0.0, target))
    return TiltResult(mix_pct=_renormalized(mix, {tilt_asset_class: clamped}))
