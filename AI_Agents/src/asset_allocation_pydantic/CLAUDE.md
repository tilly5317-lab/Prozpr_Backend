# AI_Agents/src/asset_allocation_pydantic/ — goal-based asset-allocation pipeline

Pure-Python pipeline over pydantic models: processes emergency carve-out, short / medium / long-term goals, then aggregates, applies guardrails, and assembles the presentation. LLM use is isolated to an optional rationale step.

## Entry / contract
- `run_allocation` (`__init__.py`, defined in `pipeline.py`) is the public entry. Its sibling `run_allocation_with_state` returns `(per-step state dict, output)` and is NOT re-exported from `__init__.py`, but the app's allocation bridge imports it directly from `pipeline.py` — treat its signature as a live contract.
- Input `AllocationInput`; output `GoalAllocationOutput` (asset-class-only — see invariant).
- LLM rationale is opt-OUT, not opt-in: step7 defaults to `_rationale_llm.generate_rationales` (Anthropic) when no `rationale_fn` is injected, and any failure falls through to deterministic fallbacks. Pass `_rationale_llm.no_llm_rationale_fn` to suppress the call — that is what the Master_testing runners do. Note that no production caller injects anything today, so the live allocation path lands on the LLM default.

## Files
- `__init__.py` — flat public API: `run_allocation` + the public models.
- `pipeline.py` — runs steps 1–7 in order.
- `models.py` — `AllocationInput`, `Goal`, `GoalAllocationOutput`, per-step `StepNOutput` schemas.
- `tables.py` — static lookup tables (default market-commentary scores, multi-asset composition). Asset-class only — no fund mappings.
- `utils.py` — shared rounding/helpers (`round_to_100`, `ceil_to_half`).
- `equity_subgroup_slider.py` — single source of truth for the v2 average-based equity-subgroup slider; shared with the practical engine.
- `steps/` — one file per step (`step1_emergency.py` … `step7_presentation.py`) plus `_rationale_llm.py` for the optional LLM rationale.
- `docs/plan.md` — implementation plan; planning reference, not product docs.

## Gotchas & invariants
- **Output is asset-class-only — no fund-level data.** `FUND_MAPPING` was removed from `tables.py`; `GoalAllocationOutput` must never carry fund names / ISINs / SEBI sub-category strings. Enforced by `Testing/test_no_fund_mapping.py`.
- **Phase-5 guardrail denominator.** Equity-subgroup shares are validated against `step4.multi_asset.equity_for_subgroups` — the pool left after the multi-asset carve-out, which Phase 5 itself split — NOT total equity. Wrong base silently flags valid plans (`steps/step6_guardrails.py`).
- **Symbols re-used cross-agent.** `practical_asset_allocation/` (spec §B.1, the first cross-`src/` import) imports steps 1–3 + 5, selected `step4_long_term` helpers, the slider, `tables`, `utils.round_to_100`, and the public models. Do not rename without a cross-module sweep.

## Testing
- Tests live in `Testing/` (`test_part_a.py`, `test_no_fund_mapping.py`). `Master_testing/` is a large-scale profile-sweep runner; output lands in `Master_testing/results/`.

## Don't read
- `__pycache__/`, `Testing/`, `Master_testing/results/`.
