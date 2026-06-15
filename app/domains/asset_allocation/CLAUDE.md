# app/domains/asset_allocation/ — allocation runs, buckets, aggregates, targets (persistence + reads)

## Entry / contract
- The AI bridge that *produces* allocations lives in `app/domains/ai_engine`; this domain only persists and reads them.
- `allocation_persist_service` is the single write surface, called by `ai_engine.asset_allocation_bridge`.

## Layers
- **models/** — `AssetAllocationRun` + `AssetAllocationRunTarget` + bucket children (asset_class, run_target, subgroup) + aggregate enums.
- **schemas/** — aggregate / bucket / subgroup / run payloads.
- **services/** — `asset_allocation_module_service`, `allocation_persist_service`, `allocation_recommendation_persist_service`, and `aa_engine/` — the allocation engine subpackage, documented in its own `aa_engine/CLAUDE.md`.
- No **routers/** layer yet — query endpoints are TBD.

## Don't read
- `__pycache__/`.
