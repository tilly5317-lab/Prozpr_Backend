# AI_Agents/src/asset_allocation_pydantic

Pure-Python goal-based asset-allocation pipeline over pydantic models. Processes emergency carve-out, short-term, medium-term, and long-term goals, then aggregates, applies guardrails, and assembles the final presentation. LLM use is isolated to an optional rationale function injected at the presentation step.

## Files

- `pipeline.py` — entry point for the allocation pipeline.
- `models.py` — `AllocationInput`, `Goal`, `GoalAllocationOutput`, per-step `StepNOutput` schemas.
- `tables.py` — static lookup tables (default market commentary scores, multi-asset composition, fund mappings).
- `utils.py` — shared helpers used across steps.
- `steps/` — one file per pipeline step (`step1_emergency.py` … `step7_presentation.py`) plus `_rationale_llm.py` for the optional LLM rationale.
- `docs/` — implementation plan (`plan.md`); planning reference, not product docs.
- `Testing/` — pytest suite (see Tests section).
- `Master_testing/` — large-scale profile sweep runner; results land in `Master_testing/results/`.

## Data contract

- Input: `AllocationInput`
- Output: `GoalAllocationOutput`

## Depends on

- `pydantic`; no other `src/` module is imported directly.
- `AllocationInput` carries `effective_risk_score` / OSI / `savings_rate_adjustment` fields populated from `risk_profiling/` output by the caller.
- `AllocationInput` carries a `market_commentary` score block populated from `market_commentary/` output by the caller.
- LLM use is optional via injected `rationale_fn` (Anthropic when enabled).

## Tests

- Command: `pytest AI_Agents/src/asset_allocation_pydantic/Testing -v`
- Key suites: `test_pipeline.py` (end-to-end), `test_step1_emergency.py` … `test_step7_presentation.py` (per-step), `test_tables.py`, `test_utils.py`

## Consumed by

- **`practical_asset_allocation/`** — per spec §B.1 (the first cross-agent edge
  under `AI_Agents/src/`), imports `step1_emergency.run`,
  `step2_short_term.run`, `step3_medium_term.run`, `step5_aggregation.run`,
  selected helpers from `step4_long_term` (`phase1_bounds`,
  `phase4_multi_asset`, `phase5_equity_subgroups`), `utils.round_to_100` /
  `ceil_to_half`, and the public models. **Do not rename these symbols
  without a cross-module sweep.**

## Don't read

- `__pycache__/`
- `Testing/` fixture files (`dev_output_samples.json`, `pydantic_output_samples.json`) — captured artifacts
- `Master_testing/results/` — sweep output, not source of truth
- `references/` — domain markdown consumed by prompts; not product docs
- `simulation_website/goal_allocation_explorer.html` — standalone HTML explorer, not source code
