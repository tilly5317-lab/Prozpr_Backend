"""Deterministic normalization for customer investment preferences.

Spec: docs/superpowers/specs/2026-08-24-investment-preferences-design.md,
docs/superpowers/specs/2026-09-01-investment-preferences-s1-core-design.md.
Magnitude defaults are policy, never LLM judgment.
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
    tilt_asset_class: str,
    tilt_delta_pp: float | None,
    tilt_target_pct: float | None,
) -> TiltResult:
    """Resolve a tilt into one absolute target mix.

    Baseline is the RECOMMENDED target mix (never current holdings) — the caller
    passes it in. A tilt with no number applies ``DEFAULT_TILT_STEP_PP`` toward
    the named class (``default_step_applied=True`` so the caller discloses it).
    """
    mix = {c: float(current_mix_pct.get(c, 0.0)) for c in ASSET_CLASSES}

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


# Saved-preference resolution. ONE subgroup facet: the customer sets a
# 5-state spectrum per subgroup — neutral (absent) / more / heavy / less /
# none — resolved here into a share of the WHOLE portfolio (D-A3, the one
# basis storage and the engine speak). Market-cap language (large/mid/small)
# is frontend display naming over the beta subgroups; storage and the engine
# speak subgroups only. A bare number in the map is an explicit target share
# (chat: "make gold 30%"). The step sizes are UNCHANGED across the basis
# switch (decision 2026-09-14) — a step is simply points of the portfolio now.
SUBGROUP_STEP_PP = 10.0          # "more"/"less" move the share this many points
HEAVY_STEP_PP = 20.0             # "heavy" = +20 — always strictly beyond "more"
HEAVY_SUBGROUP_FLOOR_PCT = 40.0  # ...and never below this dominant share

# Asset-class facet, same 5-state vocabulary as subgroups (option 1, 2026-09-04):
# more = +DEFAULT_TILT_STEP_PP; heavy = at least this dominant share, or
# +HEAVY_STEP_PP (20) if already above it — so Heavy is never the same result
# as More; less = -step floored at 0; none = 0. One class per
# intent — the facet is a single {class, direction} — so "only one class
# non-neutral at a time" is enforced by the wire format itself.
HEAVY_ASSET_CLASS_FLOOR_PCT = 60.0


def _resolve_subgroup_token(token, current_share_pct: float) -> float:
    if isinstance(token, (int, float)) and not isinstance(token, bool):
        return float(token)
    if token == "none":
        return 0.0
    if token == "more":
        return min(100.0, current_share_pct + SUBGROUP_STEP_PP)
    if token == "heavy":
        return min(
            100.0, max(HEAVY_SUBGROUP_FLOOR_PCT, current_share_pct + HEAVY_STEP_PP)
        )
    if token == "less":
        return max(0.0, current_share_pct - SUBGROUP_STEP_PP)
    raise ValueError(f"unknown subgroup preference token {token!r}")


@dataclass(frozen=True)
class ResolvedPreferences:
    asset_class_requested: dict[str, float] | None
    subgroup_emphasis: dict[str, float]  # 0 = hard exclusion
    applied_defaults: dict[str, str]  # telemetry/preview; persisted on the candidate row (S2d)


def resolve_saved_preferences(
    intent: dict,
    *,
    current_class_mix_pct: dict[str, float],
    current_subgroup_share_pct: dict[str, float],
) -> ResolvedPreferences:
    """Turn the screen/chat qualitative intent into stored absolutes.

    ``current_subgroup_share_pct`` carries each subgroup's current share of
    the WHOLE portfolio (needed only when a relative token — more/heavy/less
    — is present). Runs ONLY when the intent changed (caller enforces —
    re-resolving an unchanged relative intent against an already-preferred
    mix would ratchet).
    """
    applied: dict[str, str] = {}
    class_mix: dict[str, float] | None = None
    ac = intent.get("asset_class")
    if ac:
        cls = ac["class"]
        direction = ac.get("direction", "more")
        cur = float(current_class_mix_pct.get(cls, 0.0))
        delta_pp = None
        target_pct = None
        if direction == "target":
            target_pct = ac.get("target_pct")
        elif direction == "heavy":
            target_pct = max(HEAVY_ASSET_CLASS_FLOOR_PCT, cur + HEAVY_STEP_PP)
            applied["asset_class"] = f"heavy → ≥{HEAVY_ASSET_CLASS_FLOOR_PCT:.0f}%"
        elif direction == "less":
            delta_pp = -DEFAULT_TILT_STEP_PP
            applied["asset_class"] = f"-{DEFAULT_TILT_STEP_PP:.0f}pp default"
        elif direction == "none":
            target_pct = 0.0
        # "more" → both None → normalize_tilt applies the +step default
        result = normalize_tilt(
            current_class_mix_pct,
            tilt_asset_class=cls,
            tilt_delta_pp=delta_pp,
            tilt_target_pct=target_pct,
        )
        class_mix = result.mix_pct
        if result.default_step_applied:
            applied["asset_class"] = f"+{DEFAULT_TILT_STEP_PP:.0f}pp default"

    emphasis: dict[str, float] = {}
    for sg, token in (intent.get("subgroups") or {}).items():
        cur = float(current_subgroup_share_pct.get(sg, 0.0))
        emphasis[sg] = _resolve_subgroup_token(token, cur)
        if isinstance(token, str) and token != "none":
            applied[sg] = f"{token} → {emphasis[sg]:.0f}% of portfolio"

    return ResolvedPreferences(
        asset_class_requested=class_mix,
        subgroup_emphasis=emphasis,
        applied_defaults=applied,
    )
