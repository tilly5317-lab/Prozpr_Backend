# app/domains/asset_allocation/services/aa_engine/ — the allocation compute + persistence engine

## Entry / contract
- `service.py` orchestrates one allocation: build inputs → run the `asset_allocation_pydantic` 7-step pipeline in a thread → optionally persist → format chat markdown.
- Reached from the chat lifecycle via `chat.py` (registered for the `asset_allocation` intent) and from the `ai_engine` bridge.

## Files
- `service.py` — orchestrator: input building, API-key resolution, async thread offload, step tracing, optional persistence, markdown formatting.
- `input_builder.py` — builds `AllocationInput` from a `User` ORM row, reading persisted DB rows only (no live `risk_profiling` call); absent risk row → score 7.0.
- `overrides.py` — per-turn chat override allow-list (`effective_param`, `with_chat_overrides`); unknown key raises `ValueError`.
- `chat.py` — the asset_allocation-intent chat handler; classifies the turn (narrate / educate / clarify / counterfactual_explore / recompute_full / redirect) then dispatches.
- `persistence/` — SQL writes to the `asset_allocation_*` tables.
- `tests/` — pytest suite.

### persistence/
- `allocation_repository.py` — top-level `save_asset_allocation_from_engine_output`; orchestrates the writes below.
- `normalization.py` — coerces engine payloads to the canonical inner allocation dict.
- `write_asset_allocation_run.py` — inserts the parent `asset_allocation_runs` row.
- `write_asset_allocation_run_targets.py` — inserts per-run goal-snapshot target rows.
- `write_buckets.py` — inserts bucket rows + their goal-link / subgroup / asset-class children.
- `write_aggregate.py` — inserts the two run-level roll-up rows (`planned` pre-guardrail + `actual`).

## Gotchas & invariants
- **Import `chat` lazily.** It is deliberately NOT re-exported from `__init__.py` — eager import triggers a circular import via `chat_core.turn_context` (`__init__.py` docstring).
- **Rupee formatting comes from the app layer**, not the agent: `format_inr_indian` is imported from `app.domains.ai_engine.common` (`service.py`).
- **Two aggregate rows per run** — `planned` (pre-guardrail) and `actual`; do not collapse to one (`persistence/write_aggregate.py`).
- **Bare risk words are a RISK SCORE here, an equity preference in rebalancing** (`chat.py::_DETECT_SYSTEM`). "I can take more risk", no class named → `effective_risk_score`: this domain owns the risk profile, rebalancing has no risk lever so it reads the same words as `[{equity, more}]`. Naming a class or category is a preference ask on either surface. Deliberate asymmetry — spec 2026-09-16 D5.
- **A preference ask points at the preferences page; it does NOT reshape (ruling 2026-09-17).** `_dispatch_action` relays `preference_view.PREFERENCE_REDIRECT_MESSAGE` with `show_preferences_pill=True`. `_handle_preference_what_if_aa` (one engine call, contrasting against the rehydrated snapshot) is UNREFERENCED but kept as the re-enable seam, and its tests are annotated as covering that seam rather than live behaviour. SAVED preferences still shape the displayed practical allocation and are still disclosed.

## Don't read
- `__pycache__/`, `tests/`.
