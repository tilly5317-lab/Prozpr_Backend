# app/domains/practical_asset_allocation/ — holdings-aware goal-based allocation

## Entry / contract
- App-layer gateway to `AI_Agents/src/practical_asset_allocation` (holdings-aware variant: ELSS freeze, non-MF equity NFA-banded cap, v2 equity-subgroup slider).
- Runs as the **first step of the rebalancing flow** (`flow_rebalancing` in `app/domains/ai_engine/services/flow.py`) — produces the target allocation the rebalancing engine rebalances toward, handed over via the `prior` dict. No standalone chat intent.
- Also recomputed by `additional_investment`'s deficit-fill path via `compute_practical_allocation_result(..., corpus_pin=...)` — see that domain's `CLAUDE.md`.

## Layers
- **models/** — `run.py`: `PracticalAssetAllocationRun` → `practical_asset_allocation_runs`; one header row per engine run (queryable scalars + full engine output in `result_payload`).
- **schemas/** — empty (engine I/O models live in the AI_Agents module).
- **routers/** — empty (reached through the chat brain, not an HTTP route).
- **services/**
  - `practical_asset_allocation_module_service.py` — the `run(turn, ctx, prior)` AI-module gateway; dispatches to the chat handler.
  - `practical_allocation_persist_service.py` — writes one run row, flushes (the chat router owns the commit), returns the run id. Mirrors `asset_allocation`'s `allocation_recommendation_persist_service`.
  - `paa_engine/` (small, kept inline — no separate Leaf):
    - `input_builder.py` — reuses asset_allocation's `build_goal_allocation_input_for_user` for the shared `AllocationInput` fields, then adds the four practical corpus scalars (overridable via `CorpusPin`) and the resolved customer preference.
    - `service.py` — `compute_practical_allocation_result(...)` (pure-Python, no LLM) + `build_practical_fallback_brief(...)`.
    - `chat.py` — `@register("practical_asset_allocation")` first-turn handler.

## Gotchas & invariants
- **THE single preference load point for the whole system** (S1 spec §4.3, `paa_engine/input_builder.py:96`). Engines stay DB-free: the `SavedInvestmentPreference` row rides the preloaded `User.saved_investment_preference` relationship, and a per-turn dict merges field-level over it — precedence one-off > saved > none. A contract test in the profile domain (`test_contract_single_computation_reader`, GREEN since 2026-09-19) pins this as the only computation-time reader. Add a second and historical runs stop being reproducible. A read-only join off a run's `saved_investment_preference_id` is a different thing and is separately sanctioned — it recovers what shaped a past run rather than feeding a new one.
- **`apply_saved_preferences=False` produces the NEUTRAL run.** Its only production caller is the preferences screen's GET (`profile/services/screen_preference_service.py:163`), which needs Prozpr's own class recommendation — carve-outs intact — to show beside whatever the customer saved. Default it to `False` and every plan silently loses the customer's standing preference.
- **Persist is best-effort, never blocking.** `chat.py` persists every computed result, but a persistence failure is logged and swallowed — it must not block the reply or the downstream rebalancing step (`paa_engine/chat.py`).
- **Corpus scalars: MF-only defaults unless pinned.** Without a pin the whole corpus is treated as MF (`mf_corpus = total_corpus`; `non_mf_equity_corpus`, `elss_corpus = 0.0`). A caller-supplied `CorpusPin` (`paa_engine/input_builder.py:44`) overrides all four scalars; today's only source is `additional_investment`'s holdings snapshot (deficit-fill lumpsum, `ainv_engine/service.py`).

## Don't read
- `__pycache__/`.
