# app/domains/rebalancing/ — rebalancing runs, trades, fund rows, subgroup summaries, warnings. The AI bridge lives in app/domains/ai_engine

Rebalancing runs, trades, fund rows, subgroup summaries, warnings. the ai bridge lives in app/domains/ai_engine.

## Layers

- **models/** — RebalancingRun + RebalancingTrade + RebalancingFundRow + RebalancingSubgroupSummary + RebalancingWarning
- **schemas/** — rebalancing payloads (consolidates the previous flat schemas/rebalancing.py + schemas/rebalancing/* split)
- **routers/** — /rebalancing router
- **services/** — rebalancing_persist_service (called by ai_engine.rebalancing_bridge)

## Don't read

- `__pycache__/`.
