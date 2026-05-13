-- Migration: rename goal_allocation → asset_allocation + add new columns/tables.
--
-- Prerequisite: the old goal_allocation_* tables exist.
-- This script:
--   1. Renames goal_allocation_* → asset_allocation_* (+ status enum).
--   2. Renames goal_id → run_target_id on the bucket↔target join table.
--   3. Adds four nullable columns to `goals` (§5 of db_schema_asset_allocation.md).
--   4. Adds `user_id` column to `asset_allocation_bucket_subgroups`.
--   5. Creates `asset_allocation_aggregate` table (§3.4).
--
-- Backup the database before running.

BEGIN;

-- ──────────────────────────────────────────────────────────────────────────
-- 1. Rename tables: goal_allocation_* → asset_allocation_*
-- ──────────────────────────────────────────────────────────────────────────

ALTER TYPE goal_allocation_run_status_enum RENAME TO asset_allocation_run_status_enum;

ALTER TABLE goal_allocation_runs RENAME TO asset_allocation_runs;
ALTER TABLE goal_allocation_goals RENAME TO asset_allocation_run_targets;
ALTER TABLE goal_allocation_buckets RENAME TO asset_allocation_buckets;
ALTER TABLE goal_allocation_bucket_goals RENAME TO asset_allocation_bucket_run_targets;
ALTER TABLE goal_allocation_bucket_subgroups RENAME TO asset_allocation_bucket_subgroups;
ALTER TABLE goal_allocation_bucket_asset_classes RENAME TO asset_allocation_bucket_asset_classes;

-- ──────────────────────────────────────────────────────────────────────────
-- 2. Rename column: goal_id → run_target_id on the join table
-- ──────────────────────────────────────────────────────────────────────────

ALTER TABLE asset_allocation_bucket_run_targets RENAME COLUMN goal_id TO run_target_id;

-- ──────────────────────────────────────────────────────────────────────────
-- 3. goals table: add pipeline-facing columns
-- ──────────────────────────────────────────────────────────────────────────

ALTER TABLE goals ADD COLUMN IF NOT EXISTS time_to_goal_months INTEGER;
ALTER TABLE goals ADD COLUMN IF NOT EXISTS amount_needed NUMERIC(18,2);
ALTER TABLE goals ADD COLUMN IF NOT EXISTS goal_priority VARCHAR(40);
ALTER TABLE goals ADD COLUMN IF NOT EXISTS investment_goal VARCHAR(60);

-- ──────────────────────────────────────────────────────────────────────────
-- 4. asset_allocation_bucket_subgroups: add denormalized user_id
-- ──────────────────────────────────────────────────────────────────────────

ALTER TABLE asset_allocation_bucket_subgroups
    ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS ix_asset_allocation_bucket_subgroups_user_id
    ON asset_allocation_bucket_subgroups(user_id);

-- ──────────────────────────────────────────────────────────────────────────
-- 5. asset_allocation_aggregate: run-level equity/debt/others roll-up
-- ──────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS asset_allocation_aggregate (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL REFERENCES asset_allocation_runs(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    split_kind      asset_class_split_kind_enum NOT NULL,
    equity_amount   NUMERIC(18,2) NOT NULL DEFAULT 0,
    debt_amount     NUMERIC(18,2) NOT NULL DEFAULT 0,
    others_amount   NUMERIC(18,2) NOT NULL DEFAULT 0,
    equity_pct      NUMERIC(7,2)  NOT NULL DEFAULT 0,
    debt_pct        NUMERIC(7,2)  NOT NULL DEFAULT 0,
    others_pct      NUMERIC(7,2)  NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT uq_asset_allocation_aggregate_run_kind UNIQUE (run_id, split_kind)
);

CREATE INDEX IF NOT EXISTS ix_asset_allocation_aggregate_run_id
    ON asset_allocation_aggregate(run_id);

CREATE INDEX IF NOT EXISTS ix_asset_allocation_aggregate_user_id
    ON asset_allocation_aggregate(user_id);

COMMIT;
