# app/domains/asset_allocation/ — allocation runs, buckets, aggregates, targets (persistence + reads)

## Entry / contract
- Allocations are produced HERE, in `services/aa_engine/`; `asset_allocation_module_service.run(turn, ctx, prior)` is the ONLY gateway the brain calls (it lazy-imports `aa_engine/chat.py` for its `@register` side-effect, then dispatches).
- The live write surface is `aa_engine/persistence/` (`save_asset_allocation_from_engine_output`), called from `aa_engine/service.py` alongside `allocation_recommendation_persist_service`.

## Layers
- **models/** — `AssetAllocationRun` + `AssetAllocationRunTarget` + bucket children (asset_class, run_target, subgroup) + aggregate enums. The run header carries `saved_investment_preference_id` — see Gotchas.
- **schemas/** — aggregate / bucket / subgroup / run payloads.
- **services/** — `asset_allocation_module_service`, `allocation_recommendation_persist_service`, and `aa_engine/` — the allocation engine subpackage, documented in its own `aa_engine/CLAUDE.md`.
- No **routers/** layer yet — query endpoints are TBD.

## Gotchas & invariants
- **Runs are tagged with the preference row that shaped them** (`models/run.py:110`). The FK points at an IMMUTABLE `saved_investment_preferences` row, so a historical run always resolves to exactly the values it ran on; NULL means no preference. Never mutate or delete a preference row to "fix" a run — clearing only flips `is_active`.
- **The dispatcher answers a servable override even when the turn also asks to change a preference.** A cash injection or risk-score change is run and answered; the preference half becomes the shared redirect + pill (`aa_engine/chat.py`). Discarding the whole turn on the preference half was the earlier bug.
- **This engine is preference-aware only through the practical plan.** `asset_allocation_pydantic` stays Prozpr's preference-free ideal; a customer's standing preference reshapes the holdings-aware plan in `practical_asset_allocation`, not the ideal produced here.

## Don't read
- `__pycache__/`.
