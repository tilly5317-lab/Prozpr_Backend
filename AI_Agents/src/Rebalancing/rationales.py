"""Customer-facing rationale strings keyed by `reason_code`.

Each entry has a short `title` (card header) and a one-sentence `text`
(card body). Single source of truth for both the dev sweep and the
production customer-view adapter — when `app/services/rebalancing/`
is built, that module imports this map (or moves it under `app/`).

Tone: matter-of-fact, no jargon, no fund-to-goal tying (a single
recommended fund typically serves multiple buckets), no blame.
"""

from __future__ import annotations


RATIONALES: dict[str, dict[str, str]] = {
    "add_to_target": {
        "title": "Top up to target",
        "text": (
            "This fund is currently below its planned share. Adding to it keeps "
            "your investments aligned with our recommendation."
        ),
    },
    "cap_spill_buy": {
        "title": "Diversifying via alternate fund",
        "text": (
            "Your top-ranked fund in this category has reached its per-fund "
            "concentration limit. We're routing the additional amount to our "
            "next top-ranked fund in the same category to maintain diversification."
        ),
    },
    "trim_over_target": {
        "title": "Trim back to target",
        "text": (
            "This is a fund we recommend — it's just above its planned share. "
            "Only the long-held portion is being trimmed to bring the allocation "
            "back to the recommended level and keep the tax cost minimal."
        ),
    },
    "migrate_neutral_to_recommended": {
        "title": "Migrate to recommended fund",
        "text": (
            "This fund isn't in your recommended set, but the long-held portion "
            "can be moved tax-efficiently. We're shifting that portion into your "
            "recommended pick. The recently-bought portion stays put to avoid "
            "short-term capital gains tax."
        ),
    },
    "exit_bad_fund": {
        "title": "Exit — flagged for removal",
        "text": (
            "Our analysts have specifically flagged this fund for exit. The full "
            "holding is being redeemed and redeployed into your recommended set."
        ),
    },
    "exit_low_rated": {
        "title": "Exit — rating below threshold",
        "text": (
            "This fund's quality rating has fallen below our minimum threshold. "
            "Exiting it maintains the quality standard of your portfolio."
        ),
    },
    "sell_excess_direct_stocks": {
        "title": "Trim direct-stock holdings",
        "text": (
            "Your direct stock holdings exceed the level we'd recommend for "
            "your wealth bracket — concentrated single-stock positions are "
            "hard to manage well without active research. We recommend "
            "selling ₹{amount} and reallocating to diversified mutual funds, "
            "which give you the same equity exposure with much less "
            "single-name risk."
        ),
    },
}


def get_rationale(reason_code: str) -> tuple[str, str]:
    """Return `(title, text)` for a reason_code. Falls back to a humanized
    code + empty text when the code isn't in the map (defensive — should
    never happen for codes the engine actually produces)."""
    rat = RATIONALES.get(reason_code)
    if rat is not None:
        return rat["title"], rat["text"]
    return reason_code.replace("_", " ").title(), ""
