"""The ``vr`` Postgres schema — mirror tables plus three control tables.

**Nothing here is on ``Base.metadata``.** These are Core ``Table`` objects on a
private ``MetaData(schema="vr")``, which is the whole isolation story:

* ``create_all_tables()`` and Alembic ``--autogenerate`` enumerate
  ``Base.metadata``, so they cannot see, alter or propose dropping any of this.
  A vendor table can never end up in a migration diff against user data.
* The ``cas_scope`` listeners hook ``Session.do_orm_execute`` / ``before_flush``.
  Core selects and Core inserts do not pass through either, so a CAS snapshot
  scope can never silently filter vendor rows.
* There is **no foreign key from ``vr`` into ``public``**, in either direction.
  ``vr.scheme_link`` is the only bridge and it is a plain table joined at query
  time, so a bad vendor sync cannot cascade into a user's holdings and
  ``DROP SCHEMA vr CASCADE`` reverses the entire integration.

Every mirrored column is nullable and typed conservatively (see
:mod:`.specs`) — a mirror's job is to hold what the vendor sent, and a
``NOT NULL`` we invented would reject a legitimate VR row at 03:00.
"""

from __future__ import annotations

from typing import Iterable

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Index,
    Integer,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.domains.vr_data.specs import ColumnType, VrTableSpec, all_specs

VR_SCHEMA = "vr"

#: Private metadata. Intentionally NOT ``app.core.database.Base.metadata``.
VR_METADATA = MetaData(schema=VR_SCHEMA)

_TYPE_MAP = {
    "text": Text,
    "numeric": lambda: Numeric(),
    "integer": Integer,
    "date": Date,
    "timestamptz": lambda: DateTime(timezone=True),
    "jsonb": JSONB,
}


def _sa_type(kind: ColumnType):
    factory = _TYPE_MAP[kind]
    return factory()


def _build_table(spec: VrTableSpec) -> Table:
    types = spec.column_types()
    cols = [Column(name, _sa_type(types[name]), nullable=True) for name in spec.columns]
    # Bookkeeping the vendor does not send. Prefixed so it can never collide
    # with a VR field name added later.
    cols.append(
        Column(
            "_vr_synced_at",
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False,
        )
    )
    args: list = [
        PrimaryKeyConstraint(*spec.primary_key, name=f"pk_vr_{spec.name}"),
    ]
    for idx_cols in spec.indexes:
        # Postgres caps identifiers at 63 bytes; VR table names are long.
        suffix = "_".join(idx_cols)
        args.append(Index(f"ix_vr_{spec.name}_{suffix}"[:63], *idx_cols))
    return Table(spec.name, VR_METADATA, *cols, *args)


#: ``{vr table name: Core Table}`` for every declared spec, including the
#: candidates. Declaring a table is free; only the sync decides what is fetched.
MIRROR_TABLES: dict[str, Table] = {
    name: _build_table(s) for name, s in all_specs().items()
}


# ---------------------------------------------------------------------------
# control tables
# ---------------------------------------------------------------------------

#: Per-table sync watermark and last-run outcome. ``watermark`` is the highest
#: value of the spec's ``watermark_column`` we have durably stored — the next
#: run asks VR for ``changed-after`` that, so a crashed run costs a re-read of
#: one window and never a gap.
SYNC_STATE = Table(
    "sync_state",
    VR_METADATA,
    Column("table_name", String(64), primary_key=True),
    Column("watermark", String(32), nullable=True),
    Column("last_run_at", DateTime(timezone=True), nullable=True),
    Column("last_success_at", DateTime(timezone=True), nullable=True),
    Column("last_status", String(16), nullable=True),  # ok | error | running
    Column("last_error", Text, nullable=True),
    Column("last_row_count", Integer, nullable=True),
    Column("total_rows", Integer, nullable=True),
    Column("last_page_count", Integer, nullable=True),
)

#: Bulk-request budget. VR caps bulk generation at **2 per table per day** and
#: a burnt budget cannot be refunded, so every call decrements a row here
#: inside the same transaction that issues it.
BULK_BUDGET = Table(
    "bulk_budget",
    VR_METADATA,
    Column("table_name", String(64), primary_key=True),
    Column("budget_date", Date, primary_key=True),
    Column("requests_used", Integer, nullable=False, server_default="0"),
    Column("last_request_at", DateTime(timezone=True), nullable=True),
    Column("last_download_url", Text, nullable=True),
)

#: The one bridge between vendor and product data. Plain columns, no FK — a
#: join key, not a constraint, so an unresolved plan is a missing row rather
#: than a failed insert.
SCHEME_LINK = Table(
    "scheme_link",
    VR_METADATA,
    Column("plan_id", Text, primary_key=True),
    Column("scheme_code", String(20), nullable=True),
    Column("isin", String(20), nullable=True),
    Column("amfi_code", Text, nullable=True),
    Column("match_method", String(24), nullable=True),  # isin | amfi_code | manual
    Column("confidence", Numeric(), nullable=True),
    Column("linked_at", DateTime(timezone=True), server_default=func.now()),
    Index("ix_vr_scheme_link_scheme_code", "scheme_code"),
    Index("ix_vr_scheme_link_isin", "isin"),
)

CONTROL_TABLES: tuple[Table, ...] = (SYNC_STATE, BULK_BUDGET, SCHEME_LINK)


def table_for(name: str) -> Table:
    try:
        return MIRROR_TABLES[name]
    except KeyError:
        raise KeyError(f"No mirrored VR table named {name!r}") from None


def create_schema_sql() -> str:
    """The full DDL as one script, for ``migrations/sql/`` and for review.

    Emitted rather than executed so the schema lands in production the same way
    every other schema change here does — reviewed SQL, applied by hand. Alembic
    is stamped at a lost revision in this repo, so it is not an option.
    """
    from sqlalchemy.schema import CreateIndex, CreateTable
    from sqlalchemy.dialects import postgresql

    dialect = postgresql.dialect()
    out: list[str] = [
        "-- Value Research mirror. Generated by "
        "app/domains/vr_data/schema.py:create_schema_sql().",
        "-- Regenerate:  python -m scripts.vr_bootstrap --print-sql",
        "-- Reverses completely with:  DROP SCHEMA vr CASCADE;",
        "",
        f"CREATE SCHEMA IF NOT EXISTS {VR_SCHEMA};",
        "",
    ]
    tables: Iterable[Table] = (*CONTROL_TABLES, *MIRROR_TABLES.values())
    for table in tables:
        out.append(
            str(CreateTable(table, if_not_exists=True).compile(dialect=dialect)).strip()
            + ";"
        )
        for index in table.indexes:
            out.append(
                str(
                    CreateIndex(index, if_not_exists=True).compile(dialect=dialect)
                ).strip()
                + ";"
            )
        out.append("")
    return "\n".join(out)
