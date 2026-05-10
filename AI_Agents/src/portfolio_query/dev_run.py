"""
dev_run.py — Developer Smoke Test Runner
=========================================
Manually runs multi-turn test conversations through the portfolio_query pipeline.
Not a production entry point — use during development to verify the pipeline works.

HOW TO RUN
-----------
From the src/ directory:
    python -m portfolio_query.dev_run

Prerequisites:
    - ANTHROPIC_API_KEY must be set in .env (or as an env var).
    - AI_Agents/Reference_docs/market_commentary_latest.md must exist (run the
      market_commentary agent first if it doesn't).
"""

import asyncio
import os

from dotenv import load_dotenv

from .llm_client import LLMClient
from .models import (
    AllocationRow,
    ClientContext,
    ConversationTurn,
    Holding,
    PortfolioContext,
    SubCategoryAllocationRow,
)
from .orchestrator import PortfolioQueryOrchestrator


async def main():
    load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not found in .env")

    llm = LLMClient(api_key)
    orchestrator = PortfolioQueryOrchestrator(llm)

    client = ClientContext(
        age=35,
        risk_category="Moderate",
        effective_risk_score=6.5,
        investment_horizon="long-term",
        occupation_type="salaried",
        annual_income_inr=2500000.0,
        total_liabilities_inr=800000.0,
        financial_goals=["retirement", "child_education"],
    )
    portfolio = PortfolioContext(
        total_value_inr=2000000.0,
        total_invested_inr=1700000.0,
        total_gain_percentage=17.6,
        holdings=[
            Holding(
                name="Axis Bluechip Fund",
                instrument_type="MF",
                asset_class="Equity",
                sub_category="Large Cap",
                quantity=2500.0,
                current_value_inr=800000.0,
                allocation_percentage=40.0,
                return_1y_pct=18.4,
                return_3y_pct=14.2,
            ),
            Holding(
                name="Mirae Asset Mid Cap Fund",
                instrument_type="MF",
                asset_class="Equity",
                sub_category="Mid Cap",
                quantity=1500.0,
                current_value_inr=300000.0,
                allocation_percentage=15.0,
                return_1y_pct=24.1,
                return_3y_pct=19.8,
            ),
            Holding(
                name="Nippon Small Cap Fund",
                instrument_type="MF",
                asset_class="Equity",
                sub_category="Small Cap",
                quantity=800.0,
                current_value_inr=200000.0,
                allocation_percentage=10.0,
                return_1y_pct=22.0,
                return_3y_pct=21.4,
            ),
            Holding(
                name="HDFC Short Term Debt Fund",
                instrument_type="MF",
                asset_class="Debt",
                sub_category="Short Duration",
                quantity=15000.0,
                current_value_inr=500000.0,
                allocation_percentage=25.0,
                return_1y_pct=7.4,
                return_3y_pct=6.9,
            ),
            Holding(
                name="SBI Gold ETF",
                instrument_type="ETF",
                asset_class="Gold",
                sub_category="Gold",
                quantity=200.0,
                current_value_inr=200000.0,
                allocation_percentage=10.0,
                return_1y_pct=12.0,
                return_3y_pct=10.5,
            ),
        ],
        allocations=[
            AllocationRow(asset_class="Equity", percentage=65.0, amount_inr=1300000.0),
            AllocationRow(asset_class="Debt", percentage=25.0, amount_inr=500000.0),
            AllocationRow(asset_class="Gold", percentage=10.0, amount_inr=200000.0),
        ],
        sub_category_allocations=[
            SubCategoryAllocationRow(asset_class="Equity", sub_category="Large Cap", percentage=40.0, amount_inr=800000.0),
            SubCategoryAllocationRow(asset_class="Equity", sub_category="Mid Cap", percentage=15.0, amount_inr=300000.0),
            SubCategoryAllocationRow(asset_class="Equity", sub_category="Small Cap", percentage=10.0, amount_inr=200000.0),
            SubCategoryAllocationRow(asset_class="Debt", sub_category="Short Duration", percentage=25.0, amount_inr=500000.0),
            SubCategoryAllocationRow(asset_class="Gold", sub_category="Gold", percentage=10.0, amount_inr=200000.0),
        ],
    )

    print("=" * 60)
    print("  Prozpr Portfolio Query — Dev Smoke Test")
    print("=" * 60)

    # ── Scenario A: In-scope multi-turn ───────────────────────────────────────
    print("\n--- Scenario A: In-scope multi-turn conversation ---")
    history: list[ConversationTurn] = []

    in_scope_questions = [
        "What's my current allocation?",
        "How much do I have in mid cap?",
        "How is my Mirae Asset Mid Cap fund performing?",
        "What is the RBI likely to do with interest rates, and what does that mean for me?",
    ]

    for i, question in enumerate(in_scope_questions, 1):
        print(f"\n  [Turn {i}] Q: {question}")
        result = await orchestrator.run(
            question=question,
            client=client,
            portfolio=portfolio,
            conversation_history=history,
        )
        if result.guardrail_triggered:
            print(f"  Guardrail triggered: {result.redirect_message}")
        else:
            print(f"  Answer: {result.answer}")
        history.append(ConversationTurn(role="user", content=question))
        history.append(
            ConversationTurn(
                role="assistant",
                content=result.answer or result.redirect_message or "",
            )
        )

    # ── Scenario B: Guardrail triggers ────────────────────────────────────────
    print("\n--- Scenario B: Guardrail trigger tests ---")

    guardrail_questions = [
        "Should I sell my small cap and move to mid cap?",
        "How much SIP do I need to retire comfortably at 60?",
        "Can you recommend a good crypto coin to invest in?",
    ]

    for i, question in enumerate(guardrail_questions, 1):
        print(f"\n  [Test {i}] Q: {question}")
        result = await orchestrator.run(
            question=question,
            client=client,
            portfolio=portfolio,
            conversation_history=[],
        )
        if result.guardrail_triggered:
            print(f"  Guardrail triggered (expected): {result.redirect_message}")
        else:
            print(f"  Expected guardrail but got answer: {result.answer}")

    print("\n" + "=" * 60)
    print(f"  Total tokens: {llm.total_input_tokens:,} in / {llm.total_output_tokens:,} out")
    print(
        f"  Estimated cost: ~${(llm.total_input_tokens * 0.0000008 + llm.total_output_tokens * 0.000004):.4f}"
    )
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
