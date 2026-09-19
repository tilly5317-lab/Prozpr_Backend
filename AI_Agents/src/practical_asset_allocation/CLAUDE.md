# AI_Agents/src/practical_asset_allocation — holdings-aware goal-based allocation

Wraps `asset_allocation_pydantic` with four extra corpus inputs (`mf_corpus`, `non_mf_equity_corpus`, `elss_corpus`, `max_non_mf_equity_pct_client_input`) and reimplements the long-term step with ELSS freeze, non-MF equity NFA-banded cap, and the v2 sliding equity-subgroup threshold. The one engine that honours standing customer preferences.

## Entry / contract
- Entry `run_practical_allocation(input) → PracticalAllocationOutput`.
- Input extends `AllocationInput` with the four corpus scalars plus optional `human_override`.
- Output adds `corpus_breakdown` and optional `human_override_applied` to `GoalAllocationOutput`'s shape — a consumer of the latter needs zero change.

## Files
- `__init__.py` — public re-exports (entry, I/O models, `CorpusBreakdown`, `InfeasibleGoalError`).
- `pipeline.py` — all models, the orchestrator, and the long-term R157–R222 math in one file (not yet split per step).
- `human_override.py` — preference model, exclusion rule, post-run report. Pure: no I/O, DB or LLM.
- `Master_testing/`, `Testing/` — dev harness + pytest suite (both gitignored).

## Gotchas & invariants
- **Preferences are INPUTS at the phase that decides each facet, never a post-hoc reshape** (spec 2026-09-14). Class split → `phase2_asset_class_pcts`; multi-asset size → `phase4_multi_asset`; sub-groups → `phase5_equity_subgroups`. The retired "step 6" reshape ran after the sleeve was frozen, so it could not move debt — an 85/7/8 ask landed 78.33/14.00/7.67.
- **`apply_human_override` validates and reports; it no longer reshapes** (`human_override.py:150`). Always invoked, strict no-op on empty prefs — that is the golden-test guarantee. `HumanOverrideApplied` carries only `shortfall_reason`: the ask lives on the saved-preference row, what landed is the run's own breakdown.
- **Sub-group shares are % of the WHOLE portfolio (D-A3), never % of class** — the engine reads `resolved_targets` verbatim. Market-cap asks arrive on the beta sub-groups (large→low, mid→medium, small→high), matching the PAA output table.
- **An emphasis entry of `0` is a hard exclusion, not a pin of zero** (`excludes`, `human_override.py:115`) — one definition, read by both pin extraction and the customer-facing report so they cannot drift. `FROZEN_SUBGROUPS` (ELSS, direct stock) can never be refused; the engine cannot trade them.
- **Any preference suspends the steps 1–3 carve-outs** — emergency fund, near-term goals and the NFA liability offset stop being carved, and the whole corpus goes long-term (`_no_carveout_buckets`, spec 2026-09-15 §3). `carve_outs_at_risk()` serves both the screen's pre-commit warning and the run's record, so the two cannot disagree.
- **Over-subscribed pins scale proportionally; an over-large multi-asset sleeve is capped instead** (D-A4) — the sleeve is ONE fund, not a share of a class pool. A trimmed pin is never silent.
- **`asset_allocation_pydantic` is no longer diff-free** — `phase2_asset_class_pcts` takes an optional `requested_class_pcts` this orchestrator supplies. The ideal engine stays Prozpr's preference-free recommendation; preferences shape only the practical plan.
- **FIRST explicit cross-agent import** under `AI_Agents/src/` (spec §B.1): steps 1–3 + step5, selected `step4_long_term` helpers, `equity_subgroup_slider`, `tables`, `utils`. Those upstream names are a contract — a rename in any is a cross-module change. (`Rebalancing` then imports from here.)
- **Practical amounts are int/float-rounded, not `Decimal`.** The `Rebalancing` bridge coerces each lifted subgroup total with `Decimal(str(r.total))` — emit plain numbers here, do not pre-wrap.

## Testing
- `PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing -v` — the spec §B.9 scenarios.
- `Master_testing/runner.py` — dev sweep of all 5 canonical profiles through BOTH ideal and practical: `cd AI_Agents/src && python -m practical_asset_allocation.Master_testing.runner`.

## Don't read
- `__pycache__/`
- `Testing/`, `Master_testing/results/` — captured fixtures/artifacts, not source of truth.
