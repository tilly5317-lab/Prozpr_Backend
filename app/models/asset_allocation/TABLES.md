# Asset allocation — database tables

Canonical schema for persisted allocation engine output. SQLAlchemy models live in `run.py` and `bucket.py` in this package; **only these tables** are written by `save_asset_allocation_from_engine_output` and read by rebalancing cache logic.

## Table list

| Table | ORM class | Purpose |
|-------|-----------|---------|
| `asset_allocation_runs` | `AssetAllocationRun` | One row per engine execution (header + roll-ups). |
| `asset_allocation_run_targets` | `AssetAllocationRunTarget` | Frozen per-target lines from `client_summary.goals` (optional `financial_goal_id` → `goals.id`). |
| `asset_allocation_buckets` | `AssetAllocationBucket` | Per time-horizon bucket for the run. |
| `asset_allocation_bucket_run_targets` | `AssetAllocationBucketRunTarget` | M:N: bucket ↔ run target (`bucket_id`, `run_target_id`). |
| `asset_allocation_bucket_subgroups` | `AssetAllocationBucketSubgroup` | Subgroup amounts inside a bucket. |
| `asset_allocation_bucket_asset_classes` | `AssetAllocationBucketAssetClass` | Planned / actual equity–debt–others split per bucket. |

## Enum types (Postgres)

| Enum | Used on |
|------|---------|
| `asset_allocation_run_status_enum` | `asset_allocation_runs.status` |
| `allocation_bucket_name_enum` | `asset_allocation_buckets.bucket_name` |
| `asset_class_split_kind_enum` | `asset_allocation_bucket_asset_classes.split_kind` |

## Foreign keys (logical)

- `asset_allocation_runs.user_id` → `users.id`
- `asset_allocation_runs.portfolio_id` → `portfolios.id` (nullable)
- `asset_allocation_runs.chat_session_id` → `chat_sessions.id` (nullable)
- `asset_allocation_runs.supersedes_id` → `asset_allocation_runs.id` (nullable)
- `asset_allocation_run_targets.run_id` → `asset_allocation_runs.id`
- `asset_allocation_run_targets.financial_goal_id` → `goals.id` (nullable)
- `asset_allocation_buckets.run_id` → `asset_allocation_runs.id`
- `asset_allocation_bucket_run_targets.bucket_id` → `asset_allocation_buckets.id`
- `asset_allocation_bucket_run_targets.run_target_id` → `asset_allocation_run_targets.id`
- `asset_allocation_bucket_subgroups.bucket_id` → `asset_allocation_buckets.id`
- `asset_allocation_bucket_asset_classes.bucket_id` → `asset_allocation_buckets.id`
- `rebalancing_runs.source_allocation_run_id` → `asset_allocation_runs.id`

## Migrating from legacy `goal_allocation_*`

If your database still has the old names, run the SQL script:

`migrations/sql/postgres_rename_goal_allocation_to_asset_allocation.sql`

That renames tables and the status enum, and renames `goal_id` → `run_target_id` on the bucket↔target join table so it matches this document and the ORM.

## Engine JSON (not tables)

Orchestrators may still wrap the inner document with legacy keys such as `goal_allocation_output` in JSON; normalisation unwraps that before persistence. The **stored rows** always land in the tables above.
