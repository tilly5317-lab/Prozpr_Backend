"""Manual smoke-test for the Router.

Run from AI_Agents/ root:
    python -m src.router.dev_run

Tests:
  - general_market_query  → market_commentary (or graceful fallback if no cache)
  - portfolio_query       → stub
  - out_of_scope          → canned message
"""

from src.intent_classifier.models import ClassificationResult, Intent
from src.router import Router


def _make_result(
    intent: Intent,
    out_of_scope_message=None,
):
    return ClassificationResult(
        intent=intent,
        confidence=0.92,
        is_follow_up=False,
        reasoning="dev_run synthetic result",
        out_of_scope_message=out_of_scope_message,
    )


def _print_response(label, response):
    print("\n" + "=" * 60)
    print("  " + label)
    print("=" * 60)
    print("  intent      : " + response.intent.value)
    print("  module_used : " + response.module_used)
    print("  confidence  : " + str(response.confidence))
    print("  answer      : " + response.answer[:200])


def main():
    router = Router()

    # 1. general_market_query — will hit market_commentary or return graceful fallback
    _print_response(
        "general_market_query",
        router.route(
            "What is the current Nifty 50 PE ratio?",
            _make_result(Intent.GENERAL_MARKET_QUERY),
        ),
    )

    # 2. portfolio_query — stub
    _print_response(
        "portfolio_query (stub)",
        router.route(
            "What is my portfolio XIRR?",
            _make_result(Intent.PORTFOLIO_QUERY),
        ),
    )

    # 3. out_of_scope — canned message from classifier
    _print_response(
        "out_of_scope (canned)",
        router.route(
            "What is the best cricket team?",
            _make_result(
                Intent.OUT_OF_SCOPE,
                out_of_scope_message=(
                    "I'm currently set up to help with portfolio optimisation, "
                    "portfolio queries, and general market commentary."
                ),
            ),
        ),
    )

    # 4. stock_advice — canned message from classifier
    _print_response(
        "stock_advice (canned)",
        router.route(
            "Should I buy Infosys shares?",
            _make_result(
                Intent.STOCK_ADVICE,
                out_of_scope_message=(
                    "At Prozper, we don't recommend investing directly in "
                    "individual stocks."
                ),
            ),
        ),
    )

    print("\n" + "=" * 60)
    print("  dev_run complete")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
