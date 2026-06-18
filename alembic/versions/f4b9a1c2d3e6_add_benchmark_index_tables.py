"""Add benchmark_index + benchmark_index_value; migrate off index_tri_history.

Introduces the normalized benchmarks data model (catalogue + daily EOD values),
seeds the NIFTY 50 catalogue row, copies existing index_tri_history rows into it,
and drops the old flat table.

This revision also **merges the two tracked alembic heads** (`b7d4e2f1a9c3`,
`e1a2b3c4d5e6`) into one, since the original merge file
(`f3a7c1d9e2b4_add_profile_capture_fields.py`) survives only as a stale `.pyc`.

Revision ID: f4b9a1c2d3e6
Revises: b7d4e2f1a9c3, e1a2b3c4d5e6
Create Date: 2026-06-17
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f4b9a1c2d3e6"
down_revision: Union[str, Sequence[str], None] = ("b7d4e2f1a9c3", "e1a2b3c4d5e6")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Stable UUID for the seeded NIFTY 50 catalogue row (referenced by the data copy).
NIFTY50_INDEX_ID = "a0000000-0000-4000-8000-000000000050"


def upgrade() -> None:
    # gen_random_uuid() for the data-copy INSERT (core in PG13+, via pgcrypto otherwise).
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "benchmark_index",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("short_name", sa.String(length=50), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False, server_default="NSE"),
        sa.Column(
            "asset_class", sa.String(length=20), nullable=False, server_default="EQUITY"
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("earliest_available", sa.Date(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("code", name="uq_benchmark_index_code"),
    )
    op.create_index("ix_benchmark_index_code", "benchmark_index", ["code"])

    op.create_table(
        "benchmark_index_value",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "benchmark_index_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("value_date", sa.Date(), nullable=False),
        sa.Column("tri_value", sa.Numeric(14, 4), nullable=False),
        sa.Column("ntr_value", sa.Numeric(14, 4), nullable=True),
        sa.Column("pr_value", sa.Numeric(14, 4), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["benchmark_index_id"], ["benchmark_index.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "benchmark_index_id", "value_date", name="uq_benchmark_value_index_date"
        ),
    )
    op.create_index(
        "ix_benchmark_index_value_benchmark_index_id",
        "benchmark_index_value",
        ["benchmark_index_id"],
    )
    op.create_index(
        "ix_benchmark_index_value_value_date", "benchmark_index_value", ["value_date"]
    )

    # Seed the NIFTY 50 catalogue row.
    op.execute(
        sa.text(
            """
            INSERT INTO benchmark_index
                (id, code, display_name, short_name, provider, asset_class,
                 description, earliest_available, is_active, created_at, updated_at)
            VALUES
                (:id, 'NIFTY 50', 'Nifty 50', 'Nifty 50', 'NSE', 'EQUITY',
                 'NSE Nifty 50 Total Return Index (gross + net).',
                 DATE '1999-06-30', true, now(), now())
            """
        ).bindparams(id=NIFTY50_INDEX_ID)
    )

    # Copy existing flat TRI rows (if the table exists) into the new value table.
    bind = op.get_bind()
    has_old = sa.inspect(bind).has_table("index_tri_history")
    if has_old:
        op.execute(
            sa.text(
                """
                INSERT INTO benchmark_index_value
                    (id, benchmark_index_id, value_date, tri_value, ntr_value, created_at)
                SELECT gen_random_uuid(), :id, tri_date, tri_value, ntr_value, created_at
                FROM index_tri_history
                WHERE index_name = 'NIFTY 50'
                ON CONFLICT (benchmark_index_id, value_date) DO NOTHING
                """
            ).bindparams(id=NIFTY50_INDEX_ID)
        )
        op.drop_index("ix_index_tri_history_tri_date", table_name="index_tri_history")
        op.drop_index("ix_index_tri_history_index_name", table_name="index_tri_history")
        op.drop_table("index_tri_history")


def downgrade() -> None:
    # Recreate the old flat table and copy NIFTY 50 rows back.
    op.create_table(
        "index_tri_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("index_name", sa.String(length=50), nullable=False),
        sa.Column("tri_date", sa.Date(), nullable=False),
        sa.Column("tri_value", sa.Numeric(14, 4), nullable=False),
        sa.Column("ntr_value", sa.Numeric(14, 4), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("index_name", "tri_date", name="uq_index_tri_name_date"),
    )
    op.create_index(
        "ix_index_tri_history_index_name", "index_tri_history", ["index_name"]
    )
    op.create_index("ix_index_tri_history_tri_date", "index_tri_history", ["tri_date"])

    op.execute(
        sa.text(
            """
            INSERT INTO index_tri_history
                (id, index_name, tri_date, tri_value, ntr_value, created_at)
            SELECT v.id, i.code, v.value_date, v.tri_value, v.ntr_value, v.created_at
            FROM benchmark_index_value v
            JOIN benchmark_index i ON i.id = v.benchmark_index_id
            WHERE i.code = 'NIFTY 50'
            ON CONFLICT (index_name, tri_date) DO NOTHING
            """
        )
    )

    op.drop_index(
        "ix_benchmark_index_value_value_date", table_name="benchmark_index_value"
    )
    op.drop_index(
        "ix_benchmark_index_value_benchmark_index_id",
        table_name="benchmark_index_value",
    )
    op.drop_table("benchmark_index_value")
    op.drop_index("ix_benchmark_index_code", table_name="benchmark_index")
    op.drop_table("benchmark_index")
