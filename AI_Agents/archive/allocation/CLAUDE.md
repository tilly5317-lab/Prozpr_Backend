# AI_Agents/archive/allocation — ARCHIVED

Historical asset-allocation advisor pipeline. Took a client profile (and optionally a current portfolio), loaded the fund house's monthly market outlook, and produced guardrail-enforced ideal allocation ranges per asset class, a delta vs. the current portfolio, and a narrative recommendation via Claude Haiku.

Archived 2026-04-25 after being decoupled from `portfolio_query/`. The active goal-based allocation pipeline lives at `AI_Agents/src/asset_allocation_pydantic/`.

## Imported by active code?

NO

## Files

- `orchestrator.py` — `AllocationOrchestrator`; entry point for the allocation pipeline.
- `dev_run.py` — developer smoke-test runner.
- `common/` — `LLMClient`; thin Anthropic client wrapper shared across skills.
- `skills/` — `SkillExecutor` plus prompt-adjacent `.md` skill/rule sources (`guardrails.md`, `ideal_allocation.md`, `recommendation.md`); loaded at runtime, not documentation.
- `schemas/` — pydantic schemas: `ClientProfile`, `Portfolio`, `IdealAllocation`, `AllocationResponse`, `Delta`, `GuardrailBounds`, `Recommendation`.
- `utilities/` — `FundViewLoader`, `DeltaCalculator`, `ResponseFormatter`.

## Data contract

- Input: `ClientProfile` (+ optional `Portfolio`)
- Output: `AllocationResponse`

## Depends on

- `langchain-anthropic`, Claude Haiku
- `pyyaml`, `python-dotenv`
- `ANTHROPIC_API_KEY` env var; `data/fund_view.txt` for the fund view

## Don't read

- `__pycache__/`
- `skills/guardrails.md`, `skills/ideal_allocation.md`, `skills/recommendation.md` — runtime prompt sources, not documentation
