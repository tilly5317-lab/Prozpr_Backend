"""Add AA MF ingestion tables (imports, summaries, transactions)."""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d5e6f7a8b913"
down_revision: Union[str, None] = "c4d5e6f7a812"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.create_table(
        "mf_aa_imports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pan", sa.String(length=20), nullable=True),
        sa.Column("pekrn", sa.String(length=32), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("mobile", sa.String(length=20), nullable=True),
        sa.Column("from_date", sa.String(length=20), nullable=True),
        sa.Column("to_date", sa.String(length=20), nullable=True),
        sa.Column("req_id", sa.String(length=64), nullable=True),
        sa.Column("investor_first_name", sa.String(length=100), nullable=True),
        sa.Column("investor_middle_name", sa.String(length=100), nullable=True),
        sa.Column("investor_last_name", sa.String(length=100), nullable=True),
        sa.Column("address_line_1", sa.String(length=255), nullable=True),
        sa.Column("address_line_2", sa.String(length=255), nullable=True),
        sa.Column("address_line_3", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("district", sa.String(length=100), nullable=True),
        sa.Column("state", sa.String(length=100), nullable=True),
        sa.Column("pincode", sa.String(length=20), nullable=True),
        sa.Column("country", sa.String(length=100), nullable=True),
        sa.Column("source_file", sa.String(length=255), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("req_id", "email", name="uq_mf_aa_import_req_email"),
    )
    op.create_index("ix_mf_aa_imports_user_id", "mf_aa_imports", ["user_id"])
    op.create_index("ix_mf_aa_imports_pan", "mf_aa_imports", ["pan"])
    op.create_index("ix_mf_aa_imports_email", "mf_aa_imports", ["email"])
    op.execute("ALTER TABLE mf_aa_imports ALTER COLUMN id SET DEFAULT gen_random_uuid();")

    op.create_table(
        "mf_aa_summaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("aa_import_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("row_no", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("amc", sa.String(length=20), nullable=True),
        sa.Column("amc_name", sa.String(length=200), nullable=True),
        sa.Column("asset_type", sa.String(length=30), nullable=True),
        sa.Column("broker_code", sa.String(length=50), nullable=True),
        sa.Column("broker_name", sa.String(length=200), nullable=True),
        sa.Column("closing_balance", sa.Numeric(18, 3), nullable=True),
        sa.Column("cost_value", sa.Numeric(15, 2), nullable=True),
        sa.Column("decimal_amount", sa.Integer(), nullable=True),
        sa.Column("decimal_nav", sa.Integer(), nullable=True),
        sa.Column("decimal_units", sa.Integer(), nullable=True),
        sa.Column("folio", sa.String(length=40), nullable=True),
        sa.Column("is_demat", sa.String(length=5), nullable=True),
        sa.Column("isin", sa.String(length=20), nullable=True),
        sa.Column("kyc_status", sa.String(length=20), nullable=True),
        sa.Column("last_nav_date", sa.String(length=20), nullable=True),
        sa.Column("last_trxn_date", sa.String(length=20), nullable=True),
        sa.Column("market_value", sa.Numeric(15, 2), nullable=True),
        sa.Column("nav", sa.Numeric(12, 4), nullable=True),
        sa.Column("nominee_status", sa.String(length=20), nullable=True),
        sa.Column("opening_bal", sa.Numeric(18, 3), nullable=True),
        sa.Column("rta_code", sa.String(length=30), nullable=True),
        sa.Column("scheme", sa.String(length=20), nullable=True),
        sa.Column("scheme_name", sa.String(length=255), nullable=True),
        sa.Column("tax_status", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["aa_import_id"], ["mf_aa_imports.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_mf_aa_summaries_aa_import_id", "mf_aa_summaries", ["aa_import_id"])
    op.create_index("ix_mf_aa_summaries_asset_type", "mf_aa_summaries", ["asset_type"])
    op.create_index("ix_mf_aa_summaries_folio", "mf_aa_summaries", ["folio"])
    op.create_index("ix_mf_aa_summaries_isin", "mf_aa_summaries", ["isin"])
    op.create_index("ix_mf_aa_summaries_scheme", "mf_aa_summaries", ["scheme"])
    op.execute("ALTER TABLE mf_aa_summaries ALTER COLUMN id SET DEFAULT gen_random_uuid();")

    op.create_table(
        "mf_aa_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("aa_import_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("row_no", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("amc", sa.String(length=20), nullable=True),
        sa.Column("amc_name", sa.String(length=200), nullable=True),
        sa.Column("check_digit", sa.String(length=10), nullable=True),
        sa.Column("folio", sa.String(length=40), nullable=True),
        sa.Column("isin", sa.String(length=20), nullable=True),
        sa.Column("posted_date", sa.String(length=20), nullable=True),
        sa.Column("purchase_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("scheme", sa.String(length=20), nullable=True),
        sa.Column("scheme_name", sa.String(length=255), nullable=True),
        sa.Column("stamp_duty", sa.Numeric(12, 2), nullable=True),
        sa.Column("stt_tax", sa.Numeric(12, 2), nullable=True),
        sa.Column("tax", sa.Numeric(12, 2), nullable=True),
        sa.Column("total_tax", sa.Numeric(12, 2), nullable=True),
        sa.Column("trxn_amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("trxn_charge", sa.Numeric(12, 2), nullable=True),
        sa.Column("trxn_date", sa.String(length=20), nullable=True),
        sa.Column("trxn_desc", sa.String(length=100), nullable=True),
        sa.Column("trxn_mode", sa.String(length=10), nullable=True),
        sa.Column("trxn_type_flag", sa.String(length=20), nullable=True),
        sa.Column("trxn_units", sa.Numeric(18, 3), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["aa_import_id"], ["mf_aa_imports.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_mf_aa_transactions_aa_import_id", "mf_aa_transactions", ["aa_import_id"])
    op.create_index("ix_mf_aa_transactions_folio", "mf_aa_transactions", ["folio"])
    op.create_index("ix_mf_aa_transactions_isin", "mf_aa_transactions", ["isin"])
    op.create_index("ix_mf_aa_transactions_scheme", "mf_aa_transactions", ["scheme"])
    op.create_index("ix_mf_aa_transactions_trxn_date", "mf_aa_transactions", ["trxn_date"])
    op.create_index("ix_mf_aa_transactions_trxn_type_flag", "mf_aa_transactions", ["trxn_type_flag"])
    op.execute("ALTER TABLE mf_aa_transactions ALTER COLUMN id SET DEFAULT gen_random_uuid();")


def downgrade() -> None:
    op.drop_table("mf_aa_transactions")
    op.drop_table("mf_aa_summaries")
    op.drop_table("mf_aa_imports")
