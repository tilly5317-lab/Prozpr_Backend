"""
dev_run.py — Developer Smoke Test Runner
=========================================
Manually runs two end-to-end test scenarios through the Prozpr allocation advisor pipeline.
Not a production entry point — use this during development to verify the pipeline works.

HOW TO RUN
-----------
From the src/ directory:
    python -m allocation.dev_run

Prerequisites:
    - ANTHROPIC_API_KEY must be set in a .env file in the project root (or as an env var).
    - allocation/data/fund_view.txt must contain the fund house's current market outlook.

TEST SCENARIOS
---------------
Scenario A — Existing client (age 35, moderate risk, 15-year horizon)
    Has a current portfolio → pipeline computes delta and recommends adjustments.

Scenario B — New client (age 62, conservative risk, 5-year horizon)
    No portfolio → pipeline recommends a fresh allocation from scratch.

OUTPUT
-------
Each scenario prints the full AllocationResponse as JSON, including:
    - recommended_allocation: {min, max} ranges per asset class + reasoning
    - current_allocation:     existing portfolio (or null for new client)
    - delta:                  per-asset change needed (or null for new client)
    - narrative:              plain-English summary for the client
    - action_items:           what to buy/sell with fund type and reason
    - confidence:             high / medium / low
    - disclaimers:            standard SEBI-style disclaimers

A token usage and cost estimate is printed at the end.
"""

import asyncio
import json
import os
from dotenv import load_dotenv
from .common.llm_client import LLMClient
from .orchestrator import AllocationOrchestrator
from .schemas.client_profile import ClientProfile
from .schemas.portfolio import Portfolio


async def main():
    load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not found in .env")

    llm = LLMClient(api_key)
    orchestrator = AllocationOrchestrator(llm)

    print("═" * 55)
    print("  Prozpr Allocation Advisor — PoC Pipeline")
    print("═" * 55)

    # Scenario A
    print("\n━━━ Scenario A: Existing client (age 35, moderate) ━━━")
    client_a = ClientProfile(
        age=35,
        risk_profile="moderate",
        investment_horizon_years=15,
        goals=["retirement", "child_education"],
        annual_income_lakhs=25.0,
        existing_liabilities_lakhs=8.0,
    )
    portfolio_a = Portfolio(large_cap=40, mid_cap=15, small_cap=10, debt=25, gold=10)
    result_a = await orchestrator.run(client_a, portfolio_a)
    print(f"\nResult:\n{json.dumps(result_a.model_dump(), indent=2)}")

    # Scenario B
    print("\n━━━ Scenario B: New client (age 62, conservative) ━━━")
    client_b = ClientProfile(
        age=62,
        risk_profile="conservative",
        investment_horizon_years=5,
        goals=["retirement_income"],
        annual_income_lakhs=15.0,
        existing_liabilities_lakhs=2.0,
    )
    result_b = await orchestrator.run(client_b, None)
    print(f"\nResult:\n{json.dumps(result_b.model_dump(), indent=2)}")

    # Summary
    print("\n" + "═" * 55)
    print(f"  Total tokens: {llm.total_input_tokens:,} in / {llm.total_output_tokens:,} out")
    print(
        f"  Estimated cost: ~${(llm.total_input_tokens * 0.0000008 + llm.total_output_tokens * 0.000004):.4f}"
    )
    print("═" * 55)


if __name__ == "__main__":
    asyncio.run(main())
