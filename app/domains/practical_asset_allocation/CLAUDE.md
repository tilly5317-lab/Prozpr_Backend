# app/domains/practical_asset_allocation/ — holdings-aware goal-based allocation

App-layer gateway to `AI_Agents/src/practical_asset_allocation` (the
holdings-aware variant of `asset_allocation_pydantic`: ELSS freeze, non-MF
equity NFA-banded cap, v2 equity-subgroup slider). Used as the **first step of
the rebalancing flow** — it produces the target allocation the rebalancing
engine rebalances towards, handed over via the `prior` dict. There is no
standalone `practical_asset_allocation` chat intent.

## Layers

- **models/** — `run.py`: `PracticalAssetAllocationRun` →
  `practical_asset_allocation_runs`. One header row per engine run (queryable
  scalars + full engine output in `result_payload`). Every run that produces a
  result is persisted (see `chat.py` below).
- **schemas/** — empty (engine I/O models live in the AI_Agents module).
- **routers/** — empty (reached through the chat brain, not an HTTP route).
- **services/**
  - `practical_asset_allocation_module_service.py` — the `run(turn, ctx, prior)`
    AI-module gateway. Dispatches to the chat handler.
  - `practical_allocation_persist_service.py` —
    `persist_practical_allocation_run(db, ...)`. Writes one
    `practical_asset_allocation_runs` row; flushes (the chat router owns the
    commit), returns the run id. Mirrors `asset_allocation`'s
    `allocation_persist_service`.
  - `paa_engine/`
    - `input_builder.py` — `build_practical_allocation_input_for_user(ctx)`.
      Reuses asset_allocation's `build_goal_allocation_input_for_user` for the
      shared `AllocationInput` fields, then adds the four practical corpus
      scalars (`mf_corpus`, `non_mf_equity_corpus`, `elss_corpus`,
      `max_non_mf_equity_pct_client_input`).
    - `service.py` — `compute_practical_allocation_result(...)` (pure-Python
      pipeline, no LLM) + `build_practical_fallback_brief(...)`.
    - `chat.py` — `@register("practical_asset_allocation")` first-turn handler.
      After computing a result it calls `persist_practical_allocation_run`
      (best-effort: a persistence failure is logged but never blocks the reply
      or the downstream rebalancing step).

## New corpus scalars (defaults)

`PracticalAllocationInput` extends `AllocationInput`. The extra scalars have no
app-side data source yet, so they default to:

- `mf_corpus = total_corpus` — whole corpus treated as MF holdings.
- `non_mf_equity_corpus = 0.0` — direct stocks / PMS ("stocks").
- `elss_corpus = 0.0` — ELSS MF subset.
- `max_non_mf_equity_pct_client_input = None` — no advisor override.

Wire real values here when a holdings breakdown (stocks / ELSS) becomes
available.

## Flow

`rebalancing` intent → `flow_rebalancing` (`app/domains/ai_engine/services/flow.py`)
runs this module, then the rebalancing module — see `app/domains/ai_engine/CLAUDE.md`.

## Don't read

- `__pycache__/`.
