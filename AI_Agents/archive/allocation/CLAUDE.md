# AI_Agents/archive/allocation — ARCHIVED

Historical asset-allocation advisor pipeline. Took a client profile (+ optional current portfolio), loaded the fund house's monthly market outlook, and produced guardrail-enforced ideal allocation ranges per asset class, a delta vs. the current portfolio, and a Claude Haiku narrative.

Archived 2026-04-25 after being decoupled from `portfolio_query/`. The active goal-based pipeline lives at `AI_Agents/src/asset_allocation_pydantic/`.

## Imported by active code?

NO

## Files

- `orchestrator.py` — `AllocationOrchestrator`; pipeline entry point.
- `dev_run.py` — developer smoke-test runner.
- `common/` — thin Anthropic client wrapper.
- `skills/` — `SkillExecutor` + prompt-adjacent `.md` skill/rule sources, loaded at runtime (not docs).
- `schemas/` — pydantic schemas (`ClientProfile`, `Portfolio`, `AllocationResponse`, …).
- `utilities/` — fund-view loader, delta calculator, response formatter.

Data contract: `ClientProfile` (+ optional `Portfolio`) → `AllocationResponse`. Needs `langchain-anthropic` (Claude Haiku), `ANTHROPIC_API_KEY`, and a `data/fund_view.txt`.

## Don't read

- `__pycache__/`.
- `skills/*.md` — runtime prompt sources, not documentation.
