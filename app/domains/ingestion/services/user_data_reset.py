"""Full per-user financial-data reset for a clean CAMS re-ingest.

A CAMS / KFintech CAS is a *complete* snapshot of the user's mutual-fund holdings,
so re-uploading one should rebuild every downstream figure from scratch rather than
merge onto whatever stale, cached state a previous upload left behind. This module
wipes all of a user's financial and computed data in one FK-safe pass so the fresh
statement is the sole source of truth.

WHAT IS DELETED (everything derived from / computed off a portfolio):
  * portfolio container + holdings + allocations + history, daily net-worth series,
    net-worth backfill jobs;
  * the mutual-fund ledger and its audit trail (mf_transactions, mf_aa_imports +
    summaries + staging transactions, SIP mandates, per-fund snapshots, allocation
    snapshots, watchlists, user-scoped funds);
  * equity transactions;
  * asset-allocation runs (+ buckets / targets / aggregates), practical-allocation
    runs, rebalancing runs (+ trades / warnings / fund rows / summaries / totals);
  * cashflow input assumptions, one-off events, and every plan run + its rows;
  * advisory artefacts — meeting notes and the Investment Policy Statement.

WHAT IS KEPT (inputs and records, NOT recomputable cache):
  * the user account itself and all profile/onboarding tables (personal finance,
    risk, tax, investment, constraints, properties, review prefs, other investments,
    effective-risk assessment);
  * financial goals (+ contributions / holdings) — their portfolio-derived progress
    is recomputed live by the goal planner, so the definitions survive a re-upload;
  * chat sessions / messages / state / module-run telemetry;
  * notifications;
  * account links and family-member relationships (identity/login structure);
  * all GLOBAL reference data (fund metadata, NAV history, fund ratings, company
    metadata, price history, benchmark indices) — shared across users, never per-user.

GLOBAL reference tables are deliberately absent below; deleting them per-user would
both corrupt other users and fail the RESTRICT FKs that the ledger holds against them.

FK ORDERING (why this exact sequence):
  * ``rebalancing_runs`` holds a RESTRICT FK (``source_allocation_run_id``) onto
    ``asset_allocation_runs`` — rebalancing must be deleted *before* asset allocation.
  * children (run rows, buckets, plan rows, AA-import staging) are deleted before
    their parents so a missing/altered ON DELETE CASCADE (the DB is stamped at a lost
    Alembic revision) can never leave orphans or raise a constraint error.

The caller owns the surrounding transaction — this function does NOT commit, so the
wipe + the fresh re-ingest land atomically (or roll back together on failure).
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Ordered child -> parent. Each statement is scoped to the user, either directly via
# a ``user_id`` column or via a subquery on the owning parent row. Run in sequence.
_RESET_STATEMENTS: tuple[str, ...] = (
    # ── rebalancing (children, then runs — MUST precede asset_allocation_runs) ──
    "DELETE FROM rebalancing_trades WHERE run_id IN (SELECT id FROM rebalancing_runs WHERE user_id = :uid)",
    "DELETE FROM rebalancing_warnings WHERE run_id IN (SELECT id FROM rebalancing_runs WHERE user_id = :uid)",
    "DELETE FROM rebalancing_subgroup_summaries WHERE run_id IN (SELECT id FROM rebalancing_runs WHERE user_id = :uid)",
    "DELETE FROM rebalancing_fund_rows WHERE run_id IN (SELECT id FROM rebalancing_runs WHERE user_id = :uid)",
    "DELETE FROM rebalancing_totals WHERE run_id IN (SELECT id FROM rebalancing_runs WHERE user_id = :uid)",
    "DELETE FROM rebalancing_runs WHERE user_id = :uid",
    # ── asset allocation (bucket children -> buckets -> run children -> runs) ──
    "DELETE FROM asset_allocation_bucket_run_targets WHERE bucket_id IN "
    "(SELECT b.id FROM asset_allocation_buckets b JOIN asset_allocation_runs r ON b.run_id = r.id WHERE r.user_id = :uid)",
    "DELETE FROM asset_allocation_bucket_asset_classes WHERE bucket_id IN "
    "(SELECT b.id FROM asset_allocation_buckets b JOIN asset_allocation_runs r ON b.run_id = r.id WHERE r.user_id = :uid)",
    "DELETE FROM asset_allocation_bucket_subgroups WHERE bucket_id IN "
    "(SELECT b.id FROM asset_allocation_buckets b JOIN asset_allocation_runs r ON b.run_id = r.id WHERE r.user_id = :uid)",
    "DELETE FROM asset_allocation_buckets WHERE run_id IN (SELECT id FROM asset_allocation_runs WHERE user_id = :uid)",
    "DELETE FROM asset_allocation_run_targets WHERE run_id IN (SELECT id FROM asset_allocation_runs WHERE user_id = :uid)",
    "DELETE FROM asset_allocation_aggregate WHERE run_id IN (SELECT id FROM asset_allocation_runs WHERE user_id = :uid)",
    "DELETE FROM asset_allocation_runs WHERE user_id = :uid",
    # ── practical asset allocation ──
    "DELETE FROM practical_asset_allocation_runs WHERE user_id = :uid",
    # ── cashflow (plan-run rows, then plan runs, then inputs) ──
    "DELETE FROM cashflow_plan_summary WHERE plan_run_id IN (SELECT id FROM cashflow_plan_runs WHERE user_id = :uid)",
    "DELETE FROM cashflow_fund_flow_summary WHERE user_id = :uid",
    "DELETE FROM cashflow_headline WHERE user_id = :uid",
    "DELETE FROM cashflow_monthly_rows WHERE user_id = :uid",
    "DELETE FROM cashflow_annual_rows WHERE user_id = :uid",
    "DELETE FROM cashflow_plan_runs WHERE user_id = :uid",
    "DELETE FROM cashflow_input_one_off_events WHERE user_id = :uid",
    "DELETE FROM cashflow_input_assumptions WHERE user_id = :uid",
    # ── mutual funds (ledger + audit trail + per-fund caches; NOT global catalogues) ──
    "DELETE FROM mf_transactions WHERE user_id = :uid",
    "DELETE FROM mf_sip_mandates WHERE user_id = :uid",
    "DELETE FROM mf_aa_transactions WHERE aa_import_id IN (SELECT id FROM mf_aa_imports WHERE user_id = :uid)",
    "DELETE FROM mf_aa_summaries WHERE aa_import_id IN (SELECT id FROM mf_aa_imports WHERE user_id = :uid)",
    "DELETE FROM mf_aa_imports WHERE user_id = :uid",
    "DELETE FROM user_investment_lists WHERE user_id = :uid",
    "DELETE FROM user_mf_latest_snapshot WHERE user_id = :uid",
    "DELETE FROM portfolio_allocation_snapshots WHERE user_id = :uid",
    "DELETE FROM funds WHERE user_id = :uid",
    # ── equities ──
    "DELETE FROM stock_transactions WHERE user_id = :uid",
    # ── portfolio (children via portfolio_id, then containers, then user-scoped series) ──
    "DELETE FROM portfolio_history WHERE portfolio_id IN (SELECT id FROM portfolios WHERE user_id = :uid)",
    "DELETE FROM portfolio_allocations WHERE portfolio_id IN (SELECT id FROM portfolios WHERE user_id = :uid)",
    "DELETE FROM portfolio_holdings WHERE portfolio_id IN (SELECT id FROM portfolios WHERE user_id = :uid)",
    "DELETE FROM portfolios WHERE user_id = :uid",
    "DELETE FROM portfolio_networth_jobs WHERE user_id = :uid",
    "DELETE FROM user_portfolio_nav_history WHERE user_id = :uid",
    # ── advisory (meeting notes + IPS) ──
    "DELETE FROM meeting_note_items WHERE meeting_note_id IN (SELECT id FROM meeting_notes WHERE user_id = :uid)",
    "DELETE FROM meeting_notes WHERE user_id = :uid",
    "DELETE FROM investment_policy_statements WHERE user_id = :uid",
)


async def reset_user_financial_data(db: AsyncSession, user_id: uuid.UUID) -> dict[str, int]:
    """Wipe all of ``user_id``'s financial/computed data (see module docstring).

    Keeps profile/onboarding, goals, chats, notifications, account links/family, and
    all global reference data. Does NOT commit — the caller owns the transaction.

    Returns a ``{table: rows_deleted}`` map (tables that deleted 0 rows are omitted),
    useful for logging/telemetry on a re-upload.
    """
    deleted: dict[str, int] = {}
    for stmt in _RESET_STATEMENTS:
        result = await db.execute(text(stmt), {"uid": user_id})
        count = int(result.rowcount or 0)
        if count:
            # Table name is the token right after "DELETE FROM ".
            table = stmt.split("DELETE FROM ", 1)[1].split(" ", 1)[0]
            deleted[table] = deleted.get(table, 0) + count
    await db.flush()
    total = sum(deleted.values())
    logger.info(
        "reset_user_financial_data: wiped %d rows across %d tables for user %s (%s)",
        total,
        len(deleted),
        user_id,
        deleted or "nothing to wipe",
    )
    return deleted
