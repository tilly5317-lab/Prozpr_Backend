"""Guards on the properties that make this integration safe to deploy live.

These are not "does the code run" tests. Each one asserts a claim the rollout
plan makes to the business — that the mirror cannot touch user data, cannot be
picked up by a migration, and cannot silently drop a vendor field. If one of
them fails, the isolation argument is no longer true and the deploy is not safe.
"""

from __future__ import annotations

import json

import pytest

from app.core.database import Base
from app.domains.vr_data.schema import (
    CONTROL_TABLES,
    MIRROR_TABLES,
    VR_METADATA,
    VR_SCHEMA,
    create_schema_sql,
)
from app.domains.vr_data.specs import (
    CATALOG_PATH,
    DEFAULT_ENABLED_TIERS,
    all_specs,
    infer_column_type,
    specs_for_tiers,
)


# ---------------------------------------------------------------------------
# isolation
# ---------------------------------------------------------------------------


def test_vr_tables_are_not_on_base_metadata():
    """The load-bearing one.

    If a VR table ever lands on ``Base.metadata``, then ``create_all_tables()``
    creates it in ``public`` on every dev boot and Alembic autogenerate starts
    diffing vendor tables against user tables. Both are silent until they are
    catastrophic.
    """
    app_tables = set(Base.metadata.tables)
    for table in (*MIRROR_TABLES.values(), *CONTROL_TABLES):
        assert table.key not in app_tables
        assert table.schema == VR_SCHEMA


def test_no_foreign_keys_anywhere_in_the_vr_schema():
    """No FK in either direction.

    An FK from ``vr`` into ``public`` would let a vendor row block a user
    delete; one the other way would let a failed sync cascade into holdings.
    ``vr.scheme_link`` bridges the two as a plain join key instead.
    """
    for table in VR_METADATA.tables.values():
        assert not table.foreign_keys, f"{table.name} declares a foreign key"


def test_generated_ddl_touches_nothing_outside_the_vr_schema():
    sql = create_schema_sql()
    assert "DROP" not in sql.upper().replace("-- REVERSES COMPLETELY WITH:  DROP", "")
    assert "ALTER TABLE" not in sql.upper()
    assert "public." not in sql
    assert "REFERENCES" not in sql.upper()
    # Every statement is re-runnable.
    for statement in sql.split(";\n"):
        head = statement.strip()
        if head.startswith("CREATE TABLE") or head.startswith("CREATE INDEX"):
            assert "IF NOT EXISTS" in head


def test_every_mirror_table_has_a_primary_key():
    """Without one the upsert cannot dedupe and ``deleted_logs`` cannot delete."""
    for name, table in MIRROR_TABLES.items():
        assert table.primary_key.columns, f"{name} has no primary key"


# ---------------------------------------------------------------------------
# spec integrity
# ---------------------------------------------------------------------------


def test_specs_match_the_published_catalog():
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    for name, spec in all_specs().items():
        assert name in catalog, f"{name} has no catalogue entry"
        declared = [c["name"] for c in catalog[name]["columns"]]
        assert list(spec.columns) == declared
        for key in spec.primary_key:
            assert key in declared


def test_primary_keys_are_not_nullable_in_practice():
    """PK columns must be typed so a VR blank cannot become a NULL key."""
    for spec in all_specs().values():
        for key in spec.primary_key:
            assert infer_column_type(key) in {"text", "date", "integer", "numeric"}


def test_deleted_logs_is_always_enabled():
    """Skipping the deletion feed makes the mirror drift with no error.

    It must therefore be in the default tier set, and it must be in ``support``
    (never ``optional``) so no plausible ``VR_SYNC_TIERS`` value drops it while
    keeping the tables it prunes.
    """
    spec = all_specs()["deleted_logs"]
    assert spec.tier == "support"
    assert spec.tier in DEFAULT_ENABLED_TIERS


def test_cycle_order_puts_masters_first_and_deletions_last():
    order = [s.name for s in specs_for_tiers(DEFAULT_ENABLED_TIERS)]
    assert order[-1] == "deleted_logs"
    assert order.index("fund_status") < order.index("fund_basic_details")


def test_support_tier_is_exactly_what_vrs_own_fk_graph_demands():
    """Pinned so nobody re-adds masters that VR already denormalises.

    ``VR_DOCS/api_schema_relation between tables.csv`` is VR's own FK export.
    Against it, only four tables genuinely have to ride along with the CFO's
    list — everything else that looked like a required master is decodable from
    a label VR ships beside the id. Widening this silently widens the
    commercial ask.
    """
    assert {s.name for s in all_specs().values() if s.tier == "support"} == {
        "deleted_logs",
        "fund_plans",
        "subplans",
        "fund_status",
    }


def test_fund_plans_is_required_not_a_candidate():
    """Six of the requested tables foreign-key to ``fund_plans.plan_id``.

    ``fund_basic_details`` parents the holdings tables and
    ``fund_transaction_details``; ``fund_plans`` parents ``nav``,
    ``fund_sip_returns``, ``fund_returns_annual``, ``fund_dividends``,
    ``subplan_isin`` and ``rta_codes``. Taking only one master orphans half the
    feed, which is why this is not left to judgement.
    """
    assert all_specs()["fund_plans"].tier == "support"
    assert "fund_plans" in {
        s.name for s in specs_for_tiers(DEFAULT_ENABLED_TIERS)
    }


def test_candidates_are_declared_but_never_default_on():
    """Declaring the wider catalogue is free; fetching it is a contract change."""
    assert "candidate" not in DEFAULT_ENABLED_TIERS
    assert "optional" not in DEFAULT_ENABLED_TIERS
    candidates = [s for s in all_specs().values() if s.tier == "candidate"]
    assert candidates
    assert not any(s.name in {s2.name for s2 in specs_for_tiers(DEFAULT_ENABLED_TIERS)}
                   for s in candidates)


def test_the_cfo_list_is_exactly_the_core_and_additional_tiers():
    """The requested scope, pinned so a later edit cannot quietly widen it."""
    core = {s.name for s in all_specs().values() if s.tier == "core"}
    assert core == {
        "fund_basic_details",
        "subplan_isin",
        "fund_sip_returns",
        "fund_performance_details",
        "fund_holdings_details",
        "fund_holdings_aggregate_equity",
        "fund_holdings_aggregate_debt",
        "fund_transaction_details",
    }
    assert {s.name for s in all_specs().values() if s.tier == "additional"} == {"nav"}
    assert {s.name for s in all_specs().values() if s.tier == "optional"} == {
        "fund_returns_annual",
        "fund_dividends",
    }


# ---------------------------------------------------------------------------
# type inference
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "column,expected",
    [
        ("plan_id", "text"),          # an id stays text — VR never promises an int
        ("isin_code", "text"),
        ("nav", "numeric"),
        ("adjusted_nav", "numeric"),
        ("nav_date", "date"),
        ("as_on_date", "date"),
        ("year", "integer"),
        ("r5year", "numeric"),
        ("r5year_value", "numeric"),
        ("ret_1year", "numeric"),
        ("rank_365_days", "numeric"),
        ("sector_details", "jsonb"),
        ("deleted_ts", "timestamptz"),
        ("scm_sip_available", "text"),  # a Y/N flag must not become BOOLEAN
        ("status", "text"),
    ],
)
def test_column_type_inference(column, expected):
    assert infer_column_type(column) == expected


def test_ids_are_never_typed_as_integers():
    """A VR id arriving alphanumeric in a BIGINT column fails a whole page."""
    for spec in all_specs().values():
        for column in spec.columns:
            if column.endswith("_id") or column.endswith("_code"):
                assert infer_column_type(column) == "text", column


# ---------------------------------------------------------------------------
# per-table API surface
# ---------------------------------------------------------------------------


def test_every_declared_table_gets_both_read_routes():
    """The API surface is generated, so a new spec must not need new code."""
    from app.domains.vr_data.routers.vr_tables_router import router

    paths = {r.path for r in router.routes}
    for name in all_specs():
        assert f"/vr/live/{name}" in paths, f"{name} has no live route"
        assert f"/vr/db/{name}" in paths, f"{name} has no mirror route"
    assert len(paths) == 2 * len(all_specs())


def test_no_write_routes_exist_for_vendor_tables():
    """A write to a mirror is erased by the next sync, so it must not be offered.

    ``sync_service`` upserts on Value Research's own primary key: a row inserted
    or edited locally is overwritten on the next run, and a deleted row comes
    back. The only writable table is ``vr.scheme_link``, which is ours and lives
    on a different router.
    """
    from app.domains.vr_data.routers.vr_tables_router import router

    for route in router.routes:
        assert set(getattr(route, "methods", set())) <= {"GET", "HEAD"}, route.path


def test_scheme_link_is_the_only_writable_vr_table():
    from app.domains.vr_data.routers.vr_admin_router import router as admin

    writable = {
        r.path
        for r in admin.routes
        if set(getattr(r, "methods", set())) & {"PUT", "PATCH", "DELETE"}
    }
    assert writable == {"/vr/crosswalk/link"}
