# app/domains/asset_allocation/ — allocation runs, buckets, aggregates, targets. The AI bridge that *produces* these lives in app/domains/ai_engine; this domain only owns persistence and reads

Allocation runs, buckets, aggregates, targets. the ai bridge that *produces* these lives in app/domains/ai_engine; this domain only owns persistence and reads.

## Layers

- **models/** — AssetAllocationRun + AssetAllocationRunTarget + bucket children (asset_class, run_target, subgroup) + Aggregate enums
- **schemas/** — aggregate / bucket / subgroup / run payloads
- **routers/** — (empty — query endpoints are TBD)
- **services/** — allocation_persist_service (the single write surface called by ai_engine.asset_allocation_bridge) + allocation_recommendation_persist_service

## Don't read

- `__pycache__/`.
