-- One-time Postgres migration: legacy goal_allocation_* → asset_allocation_*.
-- Backup the database before running. Execute during a maintenance window.
--
-- After this script:
--   * Point application code at ORM models under app.models.asset_allocation (already done).
--   * All FKs (e.g. rebalancing_runs.source_allocation_run_id) continue to resolve; Postgres
--     updates catalog entries when tables are renamed.
--
-- Idempotency: if a table is already renamed, this will error — stop and fix manually.

BEGIN;

-- Status enum on asset_allocation_runs.status
ALTER TYPE goal_allocation_run_status_enum RENAME TO asset_allocation_run_status_enum;

-- Parent first is fine in Postgres 12+; FK metadata follows renames.
ALTER TABLE goal_allocation_runs RENAME TO asset_allocation_runs;
ALTER TABLE goal_allocation_goals RENAME TO asset_allocation_run_targets;
ALTER TABLE goal_allocation_buckets RENAME TO asset_allocation_buckets;
ALTER TABLE goal_allocation_bucket_goals RENAME TO asset_allocation_bucket_run_targets;
ALTER TABLE goal_allocation_bucket_subgroups RENAME TO asset_allocation_bucket_subgroups;
ALTER TABLE goal_allocation_bucket_asset_classes RENAME TO asset_allocation_bucket_asset_classes;

-- ORM column name run_target_id (legacy physical column was goal_id)
ALTER TABLE asset_allocation_bucket_run_targets RENAME COLUMN goal_id TO run_target_id;

COMMIT;
