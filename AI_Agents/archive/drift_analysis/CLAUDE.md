# AI_Agents/archive/drift_analysis — ARCHIVED

Historical drift-analysis pipeline. Compared actual holdings against an ideal allocation, computing drift at fund, subgroup, and asset-class levels. Deterministic, no LLM. Retained for reference; not on active import paths.

> **Dangling dependency:** imports `goal_based_allocation_pydantic` (removed from the repo — active analogue is `AI_Agents/src/asset_allocation_pydantic/`). Will not import as-is; its `Testing/` suite fails to collect.

## Imported by active code?

NO

## Files

- `pipeline.py` — entry point; orchestrates drift across all levels.
- `models.py` — `DriftInput` / `DriftOutput` and per-level drift models.
- `tables.py` — fund display-name lookups; imports from the removed `goal_based_allocation_pydantic.tables`.
- `Testing/` — pytest suite (won't collect — see dangling-dependency note).

Data contract: `DriftInput` → `DriftOutput`.

## Don't read

- `__pycache__/`.
- `Testing/sample_output.json` — captured run artifact, not source of truth.
