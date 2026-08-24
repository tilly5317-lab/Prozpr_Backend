# app/domains/asset_allocation/ — allocation runs, buckets, aggregates, targets (persistence + reads)

## Entry / contract
- Allocations are produced HERE, in `services/aa_engine/`; `asset_allocation_module_service.run(turn, ctx, prior)` is the ONLY gateway the brain calls (it lazy-imports `aa_engine/chat.py` for its `@register` side-effect, then dispatches).
- The live write surface is `aa_engine/persistence/` (`save_asset_allocation_from_engine_output`), called from `aa_engine/service.py` alongside `allocation_recommendation_persist_service`.

## Layers
- **models/** — `AssetAllocationRun` + `AssetAllocationRunTarget` + bucket children (asset_class, run_target, subgroup) + aggregate enums.
- **schemas/** — aggregate / bucket / subgroup / run payloads.
- **services/** — `asset_allocation_module_service`, `allocation_recommendation_persist_service`, and `aa_engine/` — the allocation engine subpackage, documented in its own `aa_engine/CLAUDE.md`.
- No **routers/** layer yet — query endpoints are TBD.

## Don't read
- `__pycache__/`.
