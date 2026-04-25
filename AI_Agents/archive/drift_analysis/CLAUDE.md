# AI_Agents/src/drift_analysis

Compares actual portfolio holdings against an ideal allocation, computing drift at the fund, subgroup, and asset-class levels. Produces a structured breakdown of over- and under-weight positions to support rebalancing decisions.

## Files

- `pipeline.py` — entry point; orchestrates drift computation across all levels.
- `models.py` — `ActualHolding`, `DriftInput`, `FundDrift`, `SubgroupDrift`, `AssetClassDrift`, `DriftOutput`.
- `tables.py` — fund display-name lookups and static mapping tables; imports from `goal_based_allocation_pydantic.tables`.
- `Testing/` — pytest suite (see Tests section).

## Data contract

- Input: `DriftInput`
- Output: `DriftOutput`

## Depends on

- `goal_based_allocation_pydantic/` — `tables.FUND_MAPPING` and display-name helpers
- Python stdlib (`collections`, `typing`)
- No LLM calls; fully deterministic.

## Tests

- Command: `pytest AI_Agents/src/drift_analysis/Testing -v`
- Key suites: `test_pipeline.py`

## Don't read

- `__pycache__/`
- `Testing/sample_output.json` — captured run artifact, not source of truth

## Refresh

If stale, run `/refresh-context` from this folder.
