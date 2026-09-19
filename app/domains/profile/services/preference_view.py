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

# Budgeted separately (see `_category_phrases`): the pins the customer chose
# lead, and every blank row collapses into a single "nothing in …" phrase.
_MAX_PIN_PHRASES = 2
_MAX_NAMED_EXCLUSIONS = 3  # beyond this they roll up as "and N others"
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
    """What the customer CHOSE leads; what they left blank costs one phrase.

    The two kinds are budgeted separately on purpose. The preferences screen
    writes a COMPLETE distribution over 11 settable categories, so a normal save
    carries five to eight explicit zeros. Building exclusions first and slicing
    the concatenation — as this did until 2026-09-17 — filled every slot with
    blank rows and dropped every pin the customer actually set, in JSONB
    insertion order. The reply then quoted that back as "the preferences you
    saved".

    A pin is a share of the WHOLE portfolio, so its phrase carries that basis:
    a bare "30% large-cap equity" beside an equity figure reads as 30% OF equity.
    """
    pinned = sorted(
        ((sg, pct) for sg, pct in targets.items() if pct),
        key=lambda kv: kv[1],
        reverse=True,
    )
    phrases = [
        f"{round(pct)}% of your portfolio in {subgroup_label(sg)}"
        for sg, pct in pinned[:_MAX_PIN_PHRASES]
    ]

    excluded = [subgroup_label(sg) for sg, pct in targets.items() if not pct]
    if excluded:
        named = excluded[:_MAX_NAMED_EXCLUSIONS]
        rest = len(excluded) - len(named)
        listed = named[0] if len(named) == 1 else ", ".join(named[:-1]) + f" or {named[-1]}"
        if rest:
            listed += f", and {rest} other{'s' if rest > 1 else ''}"
        phrases.append(f"nothing in {listed}")
    return phrases


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


def active_preferences_for(user: Any, practical: Any) -> Optional[dict]:
    """The facts-pack block for a plan the user's SAVED preference shaped."""
    return active_preferences_block(_active_row(user), practical)


# ---------------------------------------------------------------------------
# The chat answer for ANY preference-shaped ask (ruling 2026-09-17)
# ---------------------------------------------------------------------------
#
# Chat no longer runs preference what-ifs. Every preference-shaped ask — a
# readout, a change, an undo, or an exposure ask like "I want more equity" —
# gets this one pointer instead. Three reasons it is better here than in each
# module: the copy cannot drift across rebalancing / AA / AINV, the customer
# gets one consistent answer, and chat carries no hallucination surface for a
# stored record it cannot write.
#
# SAVED preferences still shape every plan and are still disclosed in the reply
# (see `active_preferences_block`) — only the chat-side CHANGE path is retired.

PREFERENCE_REDIRECT_MESSAGE = (
    "Your investment preferences live on your preferences page — tap below to "
    "see what's set and change it anytime. Whatever you choose there, every "
    "plan I build for you follows it."
)
