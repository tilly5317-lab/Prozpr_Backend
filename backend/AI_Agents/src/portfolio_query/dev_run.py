"""
dev_run.py — Developer Smoke Test Runner
=========================================
Manually runs multi-turn test conversations through the Prozper portfolio query pipeline.
Not a production entry point — use this during development to verify the pipeline works.

HOW TO RUN
-----------
From the src/ directory:
    python -m portfolio_query.dev_run

Prerequisites:
    - ANTHROPIC_API_KEY must be set in a .env file in the project root (or as an env var).
    - data/fund_view.txt must contain the fund house's current market outlook.

TEST SCENARIOS
---------------
Scenario A — In-scope multi-turn conversation (existing client, age 35, moderate risk)
    Turn 1: "What's my current allocation?" → factual breakdown of portfolio
    Turn 2: "How much do I have in mid cap?" → direct factual answer
    Turn 3: "How does my debt allocation compare to the market outlook?" → contextual answer
    Turn 4: "What will the RBI do with interest rates?" → market answer + Portfolio Impact paragraph
    Expected: guardrail_triggered=false for all turns; Turn 4 contains a "Portfolio Impact:" section

Scenario B — Guardrail triggers
    Q1: "Should I sell my small cap and move to mid cap?" → rebalance recommendation
    Q2: "How much SIP do I need to retire at 60?" → goal planning
    Q3: "Can you recommend a good crypto coin?" → out-of-scope financial topic
    Expected: guardrail_triggered=true for all, with appropriate redirect messages

OUTPUT
-------
Each turn prints:
    - The client's question
    - guardrail_triggered status
    - answer (if in scope) or redirect_message (if guardrail fires)
Token usage and cost estimate printed at end.
"""

import asyncio
import json
import os
from dotenv import load_dotenv
from allocation.common.llm_client import LLMClient
from allocation.schemas.client_profile import ClientProfile
from allocation.schemas.portfolio import Portfolio
from .orchestrator import PortfolioQueryOrchestrator
from .models import ConversationTurn


async def main():
    load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not found in .env")

    llm = LLMClient(api_key)
    orchestrator = PortfolioQueryOrchestrator(llm)

    # Shared client + portfolio for both scenarios
    client = ClientProfile(
        age=35,
        risk_profile="moderate",
        investment_horizon_years=15,
        goals=["retirement", "child_education"],
        annual_income_lakhs=25.0,
        existing_liabilities_lakhs=8.0,
    )
    portfolio = Portfolio(large_cap=40, mid_cap=15, small_cap=10, debt=25, gold=10)

    print("═" * 60)
    print("  Prozper Portfolio Query — PoC Pipeline")
    print("═" * 60)

    # ── Scenario A: In-scope multi-turn ───────────────────────────────────────
    print("\n━━━ Scenario A: In-scope multi-turn conversation ━━━")
    history: list[ConversationTurn] = []

    in_scope_questions = [
        "What's my current allocation?",
        "How much do I have in mid cap?",
        "How does my debt allocation compare to the market outlook?",
        "What is the RBI likely to do with interest rates, and what does that mean for me?",
    ]

    for i, question in enumerate(in_scope_questions, 1):
        print(f"\n  [Turn {i}] Q: {question}")
        result = await orchestrator.run(
            question=question,
            client_profile=client,
            current_portfolio=portfolio,
            conversation_history=history,
        )
        if result.guardrail_triggered:
            print(f"  ⚠ Guardrail triggered: {result.redirect_message}")
        else:
            print(f"  ✓ Answer: {result.answer}")
        # Append this turn to history for the next call
        history.append(ConversationTurn(role="user", content=question))
        history.append(ConversationTurn(role="assistant", content=result.answer or result.redirect_message or ""))

    # ── Scenario B: Guardrail triggers ────────────────────────────────────────
    print("\n━━━ Scenario B: Guardrail trigger tests ━━━")

    guardrail_questions = [
        "Should I sell my small cap and move to mid cap?",
        "How much SIP do I need to retire comfortably at 60?",
        "Can you recommend a good crypto coin to invest in?",
    ]

    for i, question in enumerate(guardrail_questions, 1):
        print(f"\n  [Test {i}] Q: {question}")
        result = await orchestrator.run(
            question=question,
            client_profile=client,
            current_portfolio=portfolio,
            conversation_history=[],
        )
        if result.guardrail_triggered:
            print(f"  ✓ Guardrail triggered (expected): {result.redirect_message}")
        else:
            print(f"  ✗ Expected guardrail but got answer: {result.answer}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print(f"  Total tokens: {llm.total_input_tokens:,} in / {llm.total_output_tokens:,} out")
    print(
        f"  Estimated cost: ~${(llm.total_input_tokens * 0.0000008 + llm.total_output_tokens * 0.000004):.4f}"
    )
    print("═" * 60)


if __name__ == "__main__":
    asyncio.run(main())
