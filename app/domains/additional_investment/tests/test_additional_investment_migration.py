"""Unit test for the additional_investment Alembic migration.

Loads the migration module by file path and exercises ``upgrade()`` /
``downgrade()`` against a recording fake ``op`` plus stubbed enum create/drop,
so no real Postgres and no LLM is touched (mirrors the project's
"create only the object under test, never the whole metadata" testing rule).

Asserts: the revision is chained onto the current head; both enums are created
before any table on upgrade; the three tables are created parent->child with
their indexes; and downgrade tears everything down child->parent then drops
both enums.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
MIGRATION_PATH = (
    REPO_ROOT
    / "alembic"
    / "versions"
    / "c1d2e3f4a5b6_add_additional_investment_runs.py"
)

EXPECTED_REVISION = "c1d2e3f4a5b6"
EXPECTED_DOWN_REVISION = "f5c2a1b3d8e7"  # current alembic head


class _FakeOp:
    """Records the schema operations the migration requests; runs no DDL."""

    def __init__(self, ops: list):
        self._ops = ops

    def get_bind(self):
        return "FAKE_BIND"

    def create_table(self, name, *cols, **kw):
        self._ops.append(("create_table", name))

    def create_index(self, name, table_name, columns, **kw):
        self._ops.append(("create_index", name, table_name))

    def drop_table(self, name):
        self._ops.append(("drop_table", name))

    def drop_index(self, name, table_name=None, **kw):
        self._ops.append(("drop_index", name, table_name))

    def execute(self, statement):
        self._ops.append(("execute", str(statement)))


def _load_migration(ops: list):
    """Load a fresh copy of the migration with op + enum DDL stubbed out."""
    spec = importlib.util.spec_from_file_location(
        "ainv_migration_under_test", MIGRATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # FileNotFoundError until the migration exists

    module.op = _FakeOp(ops)
    for attr in ("ADDITIONAL_INVESTMENT_TARGET_BUCKET", "ADDITIONAL_INVESTMENT_CADENCE"):
        enum_obj = getattr(module, attr)
        enum_obj.create = (
            lambda bind, checkfirst=False, _n=enum_obj.name: ops.append(
                ("enum_create", _n)
            )
        )
        enum_obj.drop = (
            lambda bind, checkfirst=False, _n=enum_obj.name: ops.append(
                ("enum_drop", _n)
            )
        )
    return module


def test_revision_is_chained_onto_current_head():
    ops: list = []
    module = _load_migration(ops)
    assert module.revision == EXPECTED_REVISION
    assert module.down_revision == EXPECTED_DOWN_REVISION


def test_upgrade_creates_both_enums_before_any_table():
    ops: list = []
    module = _load_migration(ops)
    module.upgrade()

    kinds = [row[0] for row in ops]
    first_table = kinds.index("create_table")
    enum_creates = [row[1] for row in ops if row[0] == "enum_create"]
    assert enum_creates == [
        "additional_investment_target_bucket_enum",
        "additional_investment_cadence_enum",
    ]
    assert all(
        i < first_table for i, row in enumerate(ops) if row[0] == "enum_create"
    )


def test_upgrade_creates_tables_parent_then_children_with_indexes():
    ops: list = []
    module = _load_migration(ops)
    module.upgrade()

    created_tables = [row[1] for row in ops if row[0] == "create_table"]
    assert created_tables == [
        "additional_investment_runs",
        "additional_investment_targets",
        "additional_investment_buys",
    ]

    created_indexes = {row[1] for row in ops if row[0] == "create_index"}
    assert {
        "ix_additional_investment_runs_user_id",
        "ix_additional_investment_runs_portfolio_id",
        "ix_additional_investment_runs_chat_session_id",
        "ix_additional_investment_runs_source_allocation_run_id",
        "ix_additional_investment_targets_run_id",
        "ix_additional_investment_buys_run_id",
    } <= created_indexes


def test_downgrade_drops_children_before_parent_then_enums():
    ops: list = []
    module = _load_migration(ops)
    module.downgrade()

    dropped_tables = [row[1] for row in ops if row[0] == "drop_table"]
    assert dropped_tables == [
        "additional_investment_buys",
        "additional_investment_targets",
        "additional_investment_runs",
    ]

    enum_drops = [row[1] for row in ops if row[0] == "enum_drop"]
    assert enum_drops == [
        "additional_investment_cadence_enum",
        "additional_investment_target_bucket_enum",
    ]
    last_table_drop = max(i for i, row in enumerate(ops) if row[0] == "drop_table")
    first_enum_drop = min(i for i, row in enumerate(ops) if row[0] == "enum_drop")
    assert last_table_drop < first_enum_drop
