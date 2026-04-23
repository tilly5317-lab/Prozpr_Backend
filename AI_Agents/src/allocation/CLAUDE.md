# AI_Agents/src/allocation

Asset-allocation advisor pipeline. Takes a client profile (and optionally a current portfolio), loads the fund house's monthly market outlook, and produces guardrail-enforced ideal allocation ranges per asset class, a delta vs. the current portfolio, and a narrative recommendation via Claude Haiku.

## Files

- `orchestrator.py` — `AllocationOrchestrator`; top-level 7-step pipeline controller.
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

## Refresh

If stale, run `/refresh-context` from this folder.
