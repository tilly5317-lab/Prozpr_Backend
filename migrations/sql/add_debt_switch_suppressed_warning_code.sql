-- Add DEBT_SWITCH_SUPPRESSED to the rebalancing warning enum.
--
-- Required by step2b (debt-switch netting, engine 1.2.0). The engine emits this
-- code whenever it cancels a debt-for-debt switch — on the 5-profile sweep that
-- is 4 of 5 profiles, so this is the normal path, not an edge case.
--
-- Without it, `RebalancingWarningCode(warning.code.value)` in
-- `rebalancing_persist_service.py` raises ValueError and the chat turn 500s
-- AFTER the engine has already computed a plan.
--
-- Applied as targeted DDL rather than via alembic: this environment's
-- `alembic_version` points at a revision absent from `alembic/versions/`, so
-- `alembic upgrade head` cannot run. Same treatment as the `intent` column.
--
-- MUST be applied BEFORE the engine 1.2.0 code is deployed. Safe to run more
-- than once; safe to run ahead of the deploy (the value is simply unused until
-- the new code ships).

ALTER TYPE rebalancing_warning_code_enum
    ADD VALUE IF NOT EXISTS 'DEBT_SWITCH_SUPPRESSED';

-- Verify:
--   SELECT unnest(enum_range(NULL::rebalancing_warning_code_enum));
-- Expect 5 values, including DEBT_SWITCH_SUPPRESSED.
