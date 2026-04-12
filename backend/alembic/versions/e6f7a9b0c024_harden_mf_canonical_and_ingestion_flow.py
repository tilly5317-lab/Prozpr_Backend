"""Harden MF canonical ledger and AA ingestion lifecycle."""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e6f7a9b0c024"
down_revision: Union[str, None] = "d5e6f7a8b913"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def _create_enum_if_missing(name: str, values: list[str]) -> None:
    quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in values)
    op.execute(
        f"DO $$ BEGIN CREATE TYPE {name} AS ENUM ({quoted}); "
        "EXCEPTION WHEN duplicate_object THEN null; END $$;"
    )


def upgrade() -> None:
    _create_enum_if_missing(
        "mf_transaction_source_enum", ["AA", "SIMBANKS", "MANUAL", "BACKFILL"]
    )
    _create_enum_if_missing(
        "mf_aa_import_status_enum", ["RECEIVED", "NORMALIZING", "NORMALIZED", "FAILED"]
    )

    op.add_column(
        "mf_transactions",
        sa.Column(
            "source_system",
            postgresql.ENUM(name="mf_transaction_source_enum", create_type=False),
            nullable=False,
            server_default="MANUAL",
        ),
    )
    op.add_column(
        "mf_transactions",
        sa.Column("source_import_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "mf_transactions",
        sa.Column("source_txn_fingerprint", sa.String(length=128), nullable=True),
    )
    op.create_foreign_key(
        "fk_mf_transactions_source_import_id",
        "mf_transactions",
        "mf_aa_imports",
        ["source_import_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_mf_transactions_source_system", "mf_transactions", ["source_system"])
    op.create_index(
        "ix_mf_transactions_source_import_id", "mf_transactions", ["source_import_id"]
    )
    op.create_index(
        "ix_mf_transactions_source_txn_fingerprint",
        "mf_transactions",
        ["source_txn_fingerprint"],
    )
    op.create_unique_constraint(
        "uq_mf_txn_source_fingerprint",
        "mf_transactions",
        ["source_system", "source_txn_fingerprint"],
    )
    op.create_index(
        "ix_mf_transactions_user_transaction_date",
        "mf_transactions",
        ["user_id", "transaction_date"],
    )
    op.create_index(
        "ix_mf_transactions_scheme_transaction_date",
        "mf_transactions",
        ["scheme_code", "transaction_date"],
    )
    op.alter_column("mf_transactions", "source_system", server_default=None)

    op.add_column(
        "mf_aa_imports",
        sa.Column(
            "status",
            postgresql.ENUM(name="mf_aa_import_status_enum", create_type=False),
            nullable=False,
            server_default="RECEIVED",
        ),
    )
    op.add_column(
        "mf_aa_imports",
        sa.Column("normalized_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "mf_aa_imports",
        sa.Column("failure_reason", sa.String(length=255), nullable=True),
    )
    op.create_index("ix_mf_aa_imports_status", "mf_aa_imports", ["status"])
    op.alter_column("mf_aa_imports", "status", server_default=None)

    op.create_index(
        "ix_mf_fund_metadata_amc_category",
        "mf_fund_metadata",
        ["amc_name", "category"],
    )


def downgrade() -> None:
    op.drop_index("ix_mf_fund_metadata_amc_category", table_name="mf_fund_metadata")

    op.drop_index("ix_mf_aa_imports_status", table_name="mf_aa_imports")
    op.drop_column("mf_aa_imports", "failure_reason")
    op.drop_column("mf_aa_imports", "normalized_at")
    op.drop_column("mf_aa_imports", "status")

    op.drop_index("ix_mf_transactions_scheme_transaction_date", table_name="mf_transactions")
    op.drop_index("ix_mf_transactions_user_transaction_date", table_name="mf_transactions")
    op.drop_constraint("uq_mf_txn_source_fingerprint", "mf_transactions", type_="unique")
    op.drop_index("ix_mf_transactions_source_txn_fingerprint", table_name="mf_transactions")
    op.drop_index("ix_mf_transactions_source_import_id", table_name="mf_transactions")
    op.drop_index("ix_mf_transactions_source_system", table_name="mf_transactions")
    op.drop_constraint(
        "fk_mf_transactions_source_import_id", "mf_transactions", type_="foreignkey"
    )
    op.drop_column("mf_transactions", "source_txn_fingerprint")
    op.drop_column("mf_transactions", "source_import_id")
    op.drop_column("mf_transactions", "source_system")

    op.execute("DROP TYPE IF EXISTS mf_aa_import_status_enum")
    op.execute("DROP TYPE IF EXISTS mf_transaction_source_enum")
