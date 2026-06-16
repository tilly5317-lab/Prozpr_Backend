# app/domains/practical_asset_allocation/ — holdings-aware goal-based allocation

## Entry / contract
- App-layer gateway to `AI_Agents/src/practical_asset_allocation` (holdings-aware variant: ELSS freeze, non-MF equity NFA-banded cap, v2 equity-subgroup slider).
- Runs as the **first step of the rebalancing flow** — produces the target allocation the rebalancing engine rebalances toward, handed over via the `prior` dict. No standalone chat intent.

## Layers
- **models/** — `run.py`: `PracticalAssetAllocationRun` → `practical_asset_allocation_runs`; one header row per engine run (queryable scalars + full engine output in `result_payload`).
- **schemas/** — empty (engine I/O models live in the AI_Agents module).
- **routers/** — empty (reached through the chat brain, not an HTTP route).
- **services/**
  - `practical_asset_allocation_module_service.py` — the `run(turn, ctx, prior)` AI-module gateway; dispatches to the chat handler.
  - `practical_allocation_persist_service.py` — writes one run row, flushes (the chat router owns the commit), returns the run id. Mirrors `asset_allocation`'s `allocation_persist_service`.
  - `paa_engine/` (small, kept inline — no separate Leaf):
    - `input_builder.py` — reuses asset_allocation's `build_goal_allocation_input_for_user` for the shared `AllocationInput` fields, then adds the four practical corpus scalars.
    - `service.py` — `compute_practical_allocation_result(...)` (pure-Python, no LLM) + `build_practical_fallback_brief(...)`.
    - `chat.py` — `@register("practical_asset_allocation")` first-turn handler.

## Gotchas & invariants
- **Persist is best-effort, never blocking.** `chat.py` persists every computed result, but a persistence failure is logged and swallowed — it must not block the reply or the downstream rebalancing step (`paa_engine/chat.py`).
- **The four corpus scalars have no app-side data source yet** — they default so the whole corpus is treated as MF: `mf_corpus = total_corpus`; `non_mf_equity_corpus`, `elss_corpus = 0.0`; `max_non_mf_equity_pct_client_input = None`.
- Wire real values in `input_builder.py` when a holdings breakdown (stocks / ELSS) becomes available.

## Flow
- `rebalancing` intent → `flow_rebalancing` (`app/domains/ai_engine/services/flow.py`) runs this module, then the rebalancing module — see `app/domains/ai_engine/CLAUDE.md`.

## Don't read
- `__pycache__/`.
