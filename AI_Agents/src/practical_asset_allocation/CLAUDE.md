# AI_Agents/src/practical_asset_allocation

Holdings-aware goal-based asset-allocation pipeline. Wraps
`asset_allocation_pydantic` with four extra corpus inputs (`mf_corpus`,
`non_mf_equity_corpus`, `elss_corpus`, `max_non_mf_equity_pct_client_input`)
and reimplements the long-term step with ELSS freeze, non-MF equity
NFA-banded cap, and the v2 average-based equity-subgroup sliding threshold.

Per spec §B.1 (`docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md`),
this is the **FIRST explicit cross-agent import** under `AI_Agents/src/`. The
`AI_Agents/src/CLAUDE.md` peers-only convention now has two documented
exceptions: this module imports from `asset_allocation_pydantic`, and
`Rebalancing` imports `run_practical_allocation` from this module.

## Files

- `__init__.py` — public re-exports (`run_practical_allocation`,
  `PracticalAllocationInput`, `PracticalAllocationOutput`, `CorpusBreakdown`,
  `InfeasibleGoalError`).
- `pipeline.py` — single file holding all models, the orchestrator, and the
  long-term R157–R222 math. Per design call: revisit splitting only if it
  grows past ~500 lines.
- `Testing/` — pytest suite (gitignored); the seven scenarios from spec §B.9.

## Data contract

- Input: `PracticalAllocationInput` — extends `AllocationInput` with four new
  corpus scalars (`mf_corpus`, `non_mf_equity_corpus`, `elss_corpus`,
  `max_non_mf_equity_pct_client_input`); all other fields inherited unchanged.
- Output: `PracticalAllocationOutput` — shape-parity with `GoalAllocationOutput`
  plus one `corpus_breakdown` block. Any consumer that already understands
  `GoalAllocationOutput` handles `PracticalAllocationOutput` for the shared
  seven fields with zero change.

## Depends on

- `pydantic` only at the third-party level.
- **Cross-agent imports from `asset_allocation_pydantic`** (the spec-blessed
  exception to the peers-only rule):
  - `steps.step1_emergency.run`, `step2_short_term.run`, `step3_medium_term.run`,
    `step5_aggregation.run`.
  - `steps.step4_long_term.phase1_bounds`, `phase4_multi_asset`,
    `phase5_equity_subgroups`.
  - `utils.round_to_100`, `ceil_to_half`.
  - `models.AllocationInput`, `Goal`, `MarketCommentaryScores`,
    `MultiAssetFundComposition`, `BucketAllocation`, `AggregatedSubgroupRow`,
    `FutureInvestment`, `ClientSummary`, `AssetClassBreakdown`,
    `AssetClassSplitBlock`, `BucketAssetClassSplit`, `Step1Output`,
    `Step2Output`, `Step3Output`, `Step4Output`, `Step5Output`.
  - These names must remain stable on the upstream side; renames there are a
    cross-module change.

## Tests

- Command: `PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing -v`
- Covers the seven scenarios from spec §B.9 (regression-vs-ideal, ELSS-below-equity,
  ELSS-lifts-above-ideal, non-MF below cap, non-MF above cap, sliding-threshold v2,
  mid-sequence underfunding).

## Don't read

- `__pycache__/`
- `Testing/` artifacts — captured fixtures, not source of truth.
