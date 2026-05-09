# AI_Agents/archive/drift_analysis — ARCHIVED

Historical drift-analysis pipeline. Compared actual portfolio holdings against an ideal allocation, computing drift at the fund, subgroup, and asset-class levels. Retained for reference; not on active import paths.

> **Dangling dependency:** imports `goal_based_allocation_pydantic` (which has been removed from the repo — the active analogue is `AI_Agents/src/asset_allocation_pydantic/`). This archived module will not import as-is.

## Imported by active code?

NO

## Files

- `pipeline.py` — entry point; orchestrates drift computation across all levels.
- `models.py` — `ActualHolding`, `DriftInput`, `FundDrift`, `SubgroupDrift`, `AssetClassDrift`, `DriftOutput`.
- `tables.py` — fund display-name lookups and static mapping tables; imports from `goal_based_allocation_pydantic.tables`.
- `Testing/` — pytest suite (see Tests section).

## Data contract

- Input: `DriftInput`
- Output: `DriftOutput`

## Historical dependencies

- `goal_based_allocation_pydantic/` — `tables.FUND_MAPPING` and display-name helpers (module no longer exists in the repo)
- Python stdlib (`collections`, `typing`)
- No LLM calls; fully deterministic.

## Tests

- Command: `pytest AI_Agents/archive/drift_analysis/Testing -v` (will fail to collect — see dangling dependency note above).
- Key suites: `test_pipeline.py`

## Don't read

- `__pycache__/`
- `Testing/sample_output.json` — captured run artifact, not source of truth
