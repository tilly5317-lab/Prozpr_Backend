# Asset allocation persistence — data flow

Persistence **never imports** `AI_Agents`. The bridge passes whatever the orchestrator already produced (`dict`, wrapper with ``allocation_output`` or legacy ``goal_allocation_output``, or a Pydantic model with ``model_dump()``).

**ORM** for these tables lives in ``app.models.asset_allocation`` (not ``app.models.goals``). **Postgres** tables use the ``asset_allocation_*`` prefix — see ``app/models/asset_allocation/TABLES.md``. Legacy DBs should run ``migrations/sql/postgres_rename_goal_allocation_to_asset_allocation.sql`` once.

Diagrams use the viewer’s default Mermaid theme. Unicode sketch follows for plain-text viewers.

---

## 1. Write path — engine output → database

### Mermaid

```mermaid
%%{init: {'flowchart': {'htmlLabels': true, 'curve': 'basis'}}}%%
flowchart TB
  subgraph PACK["◧ External engine (not imported by persistence)"]
    direction TB
    ORCH[["`AI_Agents/src/… allocation pipeline`"]]
    ER[["`engine_result` · dict | Pydantic`"]]
    ORCH ==>|returns| ER
  end

  subgraph SVC["◧ asset_allocation / service.py"]
    CAR[["`compute_allocation_result()`"]]
    GATE{"`persist_recommendation ∧ db ?`"}
    CAR --> GATE
    GATE -->|yes| REPO_CALL
    GATE -.->|no| NOSAVE[("skip persistence")]
  end

  subgraph REPO_FILE["◧ persistence / allocation_repository.py"]
    REPO_CALL[["`save_asset_allocation_from_engine_output()`"]]
    NORM[["`normalization.py` → normalize_asset_allocation_engine_result()`"]]
    RES[["`_resolve_portfolio_id()`"]]
    MERGE(("`merge inputs\nfor inserts`"))
    REPO_CALL --> NORM
    REPO_CALL --> RES
    NORM --> MERGE
    RES --> MERGE
  end

  subgraph WR1["◧ write_asset_allocation_run.py"]
    WRUN[["`insert_asset_allocation_run()`"]]
  end
  subgraph WR2["◧ write_asset_allocation_run_targets.py"]
    WGOAL[["`insert_asset_allocation_run_targets_for_run()`"]]
  end
  subgraph WR3["◧ write_buckets.py"]
    WBUCK[["`insert_buckets_and_children()`"]]
  end

  subgraph PG["◧ PostgreSQL (asset_allocation_* tables)"]
    direction TB
    T1[("asset_allocation_runs")]
    T2[("asset_allocation_run_targets")]
    T3[("asset_allocation_buckets")]
    T4[("asset_allocation_bucket_run_targets")]
    T5[("asset_allocation_bucket_subgroups")]
    T6[("asset_allocation_bucket_asset_classes")]
  end

  ER ==>|in-process hand-off| CAR
  MERGE ==> WRUN
  WRUN ==> WGOAL
  WGOAL ==> WBUCK
  WRUN --> T1
  WGOAL --> T2
  WBUCK --> T3
  WBUCK --> T4
  WBUCK --> T5
  WBUCK --> T6
```

| Syntax | Meaning |
|--------|---------|
| `==>` | Primary data / control |
| `-->` | Normal FK write |
| `-.->` | Optional path |

**Combine / divert**

- **Combine:** normalisation and portfolio resolution both feed the insert chain (merge before ``insert_asset_allocation_run``).
- **Divert:** persistence only runs when ``persist_recommendation`` and ``db`` are set.

### Unicode — write path

```
  ┌──────────────────────────────────────────────┐
  │ AI_Agents/… (engine — not imported here)    │
  │  pipeline ──▶ engine_result                 │
  └───────────────────────────┬────────────────┘
                              ▼
  ┌──────────────────────────────────────────────┐
  │ asset_allocation / service.py               │
  │  compute_allocation_result                  │
  │         ▼                                   │
  │    ◇ persist? ◇──no──▶ (skip)               │
  │         │ yes                               │
  └─────────┼──────────────────────────────────┘
            ▼
  ┌──────────────────────────────────────────────┐
  │ persistence / allocation_repository.py      │
  │  save_asset_allocation_from_engine_output   │
  │    ├─▶ normalize_asset_allocation…          │
  │    └─▶ _resolve_portfolio_id ──┐            │
  │              merge ────────────┘            │
  └───────────────┬────────────────────────────┘
                  ▼
    write_asset_allocation_run.py ──▶ asset_allocation_runs
                  ▼
    write_asset_allocation_run_targets.py ──▶ asset_allocation_run_targets
                  ▼
    write_buckets.py             ──▶ buckets + joins + subgroups + asset_classes
```

### Step reference

| Step | Function | Responsibility |
|------|----------|----------------|
| 1 | *(allocation package in `AI_Agents/src/…`)* | Runs engine; returns payload. |
| 2 | `compute_allocation_result` | Bridge entry; optional gates; may call save. |
| 3 | `save_asset_allocation_from_engine_output` | Orchestrates inserts (caller owns `commit`). |
| 4 | `normalize_asset_allocation_engine_result` | Inner dict from wrapper / `model_dump()`. |
| 5 | `_resolve_portfolio_id` | Primary (or first) portfolio id. |
| 6 | `insert_asset_allocation_run` | Header row on ``asset_allocation_runs``. |
| 7 | `insert_asset_allocation_run_targets_for_run` | Per-target lines on ``asset_allocation_run_targets`` (optional FK to user ``goals``). |
| 8 | `insert_buckets_and_children` | Bucket tree + subgroup + asset-class rows. |

---

## 2. Read path — ORM → rebalancing

Subgroup INR targets are rebuilt from **normalised rows** (``asset_allocation_bucket_subgroups.actual_amount`` summed per ``subgroup`` for the latest ``asset_allocation_runs`` row), not from snapshot JSON.

```mermaid
flowchart LR
  subgraph TABLES["◧ PostgreSQL"]
    direction TB
    R[("asset_allocation_runs")]
    SG[("asset_allocation_bucket_subgroups\n(+ buckets via FK)")]
  end

  subgraph RB["◧ rebalancing/"]
    L[["`service.py` · `_load_cached_allocation()`"]]
    P[["`cached_allocation.py` · try_parse_asset_allocation_json()`"]]
    B[["`input_builder.py` · build_rebalancing_input_for_user()`"]]
  end

  R -->|latest run ≤ TTL| L
  SG -->|JOIN + GROUP BY subgroup\nSUM(actual_amount)| L
  L ==>|mini JSON `{ aggregated_subgroups }`| P
  P -->|CachedAssetAllocationView| B
```

### Unicode — read path

```
  asset_allocation_runs ─────┐
                            ├──▶ rebalancing/service.py · _load_cached_allocation
  asset_allocation_buckets ─┤
         │                  │
         └── bucket_subgroups (aggregate per subgroup)
                            │
                            ▼
                 cached_allocation.py · try_parse_asset_allocation_json
                            │
                            ▼
                 input_builder.py · build_rebalancing_input_for_user
```

| Step | Function | Responsibility |
|------|----------|----------------|
| 1 | `_load_cached_allocation` | Latest ``AssetAllocationRun`` + SQL aggregate → JSON shape. |
| 2 | `try_parse_asset_allocation_json` | ``CachedAssetAllocationView``. |
| 3 | `build_rebalancing_input_for_user` | ``.aggregated_subgroups`` → per–``asset_subgroup`` targets. |

---

## 3. Upstream (inputs to the engine)

When the allocation package is wired again, **ORM → agent DTO** mapping stays outside `persistence/` (e.g. ``build_asset_allocation_input_for_user`` in ``asset_allocation/input_builder.py``). That path feeds **into** the engine; this folder handles **engine output → ``asset_allocation_*`` tables** only.
