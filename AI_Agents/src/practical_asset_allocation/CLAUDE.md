# AI_Agents/src/practical_asset_allocation — holdings-aware goal-based allocation

Wraps `asset_allocation_pydantic` with four extra corpus inputs (`mf_corpus`, `non_mf_equity_corpus`, `elss_corpus`, `max_non_mf_equity_pct_client_input`) and reimplements the long-term step with ELSS freeze, non-MF equity NFA-banded cap, and the v2 average-based equity-subgroup sliding threshold.

## Entry / contract
- Entry `run_practical_allocation(input) → PracticalAllocationOutput`.
- Input extends `AllocationInput` with the four corpus scalars; other fields inherited unchanged.
- Output is shape-parity with `GoalAllocationOutput` plus one `corpus_breakdown` block — a consumer that reads `GoalAllocationOutput` handles the shared fields with zero change.

## Files
- `__init__.py` — public re-exports (`run_practical_allocation`, the I/O models, `CorpusBreakdown`, `InfeasibleGoalError`).
- `pipeline.py` — the single combined file: all models, the orchestrator, and the long-term R157–R222 math (not yet split into per-step modules).
- `Master_testing/`, `Testing/` — dev harness + pytest suite (both gitignored). See `## Testing`.

## Gotchas & invariants
- **FIRST explicit cross-agent import** under `AI_Agents/src/` (spec §B.1) — imports steps 1–3, step5, selected step4 helpers, utils, and models from `asset_allocation_pydantic`. Those upstream names are a contract: a rename there is a cross-module change. (`Rebalancing` then imports from here.)
- **Practical amounts are int/float-rounded, not `Decimal`.** Downstream `Rebalancing` rows are `Decimal`; the bridge coerces each lifted subgroup total with `Decimal(str(r.total))` (`Rebalancing/pipeline.py`, `_assign_subgroup_targets`). Emit plain numbers here — do not pre-wrap in `Decimal`.

## Testing
- Unit/scenario: `PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing -v` — covers the spec §B.9 scenarios (regression-vs-ideal, ELSS bands, non-MF cap, sliding-threshold v2, mid-sequence underfunding).
- `Master_testing/runner.py` — dev sweep running all 5 canonical profiles through BOTH ideal (`asset_allocation_pydantic`) and practical, dumping combined `results/results.json` + `summary.md` + self-contained `results.html`. Invoke: `cd AI_Agents/src && python -m practical_asset_allocation.Master_testing.runner`.

## Don't read
- `__pycache__/`
- `Testing/`, `Master_testing/results/` — captured fixtures/artifacts, not source of truth.
