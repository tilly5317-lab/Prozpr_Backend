"""The single reader of a saved investment-preference row (spec 2026-09-16).

``customer_choices`` has two shapes: chat writes ``{asset_class, subgroups}``
with relative tokens, the preferences screen writes ``{class_mix, pins}``. Four
call sites used to read the column directly and each understood only its own
shape, which is how a chat what-if came to discard a screen-saved preference
entirely. Everything that asks "what did the customer choose" reads through here.
"""

from __future__ import annotations

from typing import Any, Optional

from app.domains.additional_investment.services.lumpsum_reasoning import subgroup_label

_CHAT_KEYS = ("asset_class", "subgroups")

_MAX_CATEGORY_PHRASES = 3  # + the class-mix phrase = 4 total
# ``others`` is the engine's name for the third class; the customer's is
# Commodity — the preferences screen validates with exactly that word.
_CLASS_WORDS = {"equity": "equity", "debt": "debt", "others": "commodity"}


def _chat_shaped(choices: Optional[dict]) -> bool:
    return bool(choices) and any(k in choices for k in _CHAT_KEYS)


def canonical_intent(row: Any) -> dict:
    """``{asset_class?, subgroups?}`` for any saved-preference row.

    A chat-written row passes through untouched: its relative tokens ("more",
    "heavy") are what ``_changed_intent``'s anti-ratchet guard compares, and
    resolving them to numbers would ratchet the preference on every repeat ask.
    Any other row derives from the TYPED columns — ``resolved_targets`` is the
    engine's only subgroup input, so the merge then composes over what the engine
    actually did, and a future screen-payload change cannot break this again.
    """
    if row is None:
        return {}
    choices = getattr(row, "customer_choices", None) or None
    if _chat_shaped(choices):
        return {k: v for k, v in choices.items() if v not in (None, [], {})}

    out: dict[str, Any] = {}
    mix = getattr(row, "asset_class_requested", None)
    if mix:
        out["asset_class"] = dict(mix)
    # A 0.0 target is a hard exclusion — keep it. Only an absent/empty mapping
    # means "engine decides".
    targets = getattr(row, "resolved_targets", None)
    if targets:
        out["subgroups"] = dict(targets)
    return out


def _class_phrase(mix: Optional[dict]) -> Optional[str]:
    if not mix:
        return None
    parts = [
        f"{round(mix[cls])}% {word}"
        for cls, word in _CLASS_WORDS.items()
        if mix.get(cls) is not None
    ]
    return " / ".join(parts) or None


def _category_phrases(targets: dict) -> list[str]:
    """Exclusions first (the most load-bearing fact), then the largest pins.

    A pin is a share of the WHOLE portfolio, so the phrase carries its basis:
    "30% large-cap equity" next to an equity figure would read as 30% OF equity.
    """
    excluded = [f"no {subgroup_label(sg)}" for sg, pct in targets.items() if not pct]
    pinned = sorted(
        ((sg, pct) for sg, pct in targets.items() if pct),
        key=lambda kv: kv[1],
        reverse=True,
    )
    phrases = [
        f"{round(pct)}% of your portfolio in {subgroup_label(sg)}" for sg, pct in pinned
    ]
    return (excluded + phrases)[:_MAX_CATEGORY_PHRASES]


def describe(row: Any) -> Optional[list[str]]:
    """The row as customer-facing phrases. None when there is nothing to say.

    Numbers come from the typed columns, so the wording is identical whichever
    surface wrote the row; the vocabulary is ``subgroup_label``, the same table
    the preferences screen renders from.
    """
    if row is None:
        return None
    choices: list[str] = []
    class_phrase = _class_phrase(getattr(row, "asset_class_requested", None))
    if class_phrase:
        choices.append(class_phrase)
    choices.extend(_category_phrases(getattr(row, "resolved_targets", None) or {}))
    return choices or None


def active_preferences_block(row: Any, practical: Any) -> Optional[dict]:
    """The facts-pack block for a plan shaped by a SAVED preference.

    ``applied`` / ``shortfall_reason`` come from the RUN
    (``human_override_applied``), never the row, so they stay true for the plan
    in front of the customer. An output without that field — AA's
    preference-free ideal fallback — yields None rather than a claim.
    """
    applied = getattr(practical, "human_override_applied", None)
    if applied is None or not getattr(applied, "preference_applied", False):
        return None
    choices = describe(row)
    if choices is None:
        return None
    return {
        "choices": choices,
        "applied": True,
        "shortfall_reason": getattr(applied, "shortfall_reason", None),
    }


# ── The cross-domain surface. Other domains call THESE, never the relationship:
# `test_contract_single_computation_reader` allows only sanctioned modules to
# read `user.saved_investment_preference`, and chat modules are not among them.


def _active_row(user: Any) -> Any:
    """The user's active preference row off the eager-loaded relationship. The
    ONE place outside the save path that reads it (no DB round trip —
    ``load_user_for_ai`` selectinloads it)."""
    return getattr(user, "saved_investment_preference", None)


def describe_active(user: Any) -> Optional[list[str]]:
    """The user's saved preference in customer words, for a readout."""
    return describe(_active_row(user))


def active_preferences_for(user: Any, practical: Any) -> Optional[dict]:
    """The facts-pack block for a plan the user's SAVED preference shaped."""
    return active_preferences_block(_active_row(user), practical)
