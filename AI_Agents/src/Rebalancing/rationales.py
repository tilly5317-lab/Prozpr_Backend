"""Customer-facing rationale strings keyed by `reason_code`.

Each entry has a short `title` (card header) and a one-sentence `text`
(card body). Single source of truth for both the dev sweep and the
production customer-view adapter — when `app/services/rebalancing/`
is built, that module imports this map (or moves it under `app/`).

Tone: matter-of-fact, no jargon, ties each action back to the
customer's plan/goals, no blame.
"""

from __future__ import annotations


RATIONALES: dict[str, dict[str, str]] = {
    "add_to_target": {
        "title": "Top up to target",
        "text": (
            "This fund is currently below its planned share of your portfolio. "
            "Adding to it keeps your investments aligned with the allocation "
            "we've recommended for your goals."
        ),
    },
    "cap_spill_buy": {
        "title": "Diversifying via alternate fund",
        "text": (
            "Your top-ranked fund in this category has reached its per-fund "
            "concentration limit. We're routing the additional amount to the "
            "next-ranked fund in the same category to maintain diversification."
        ),
    },
    "trim_over_target": {
        "title": "Trim back to target",
        "text": (
            "This fund is currently above its planned share of your portfolio. "
            "Trimming brings the allocation back in line with the recommended "
            "plan for your goals."
        ),
    },
    "exit_bad_fund": {
        "title": "Exit — not in recommended list",
        "text": (
            "This fund is not part of our current recommended portfolio. "
            "Exiting frees the capital to be redeployed into funds aligned "
            "with your plan."
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
