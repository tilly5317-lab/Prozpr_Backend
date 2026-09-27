"""The held-fund NAV lookup must stay two indexed branches — never one ``OR``.

An ``OR`` across ``scheme_code`` and ``upper(isin)`` combined with
``ORDER BY nav_date DESC LIMIT 1`` makes Postgres abandon both indexes and walk
``ix_mf_nav_history_nav_date`` backwards with the key as a mere *filter*. Against ~14M
rows / 4.85 GB a key that is absent — an ISIN the ingest could not resolve — then costs
~5 minutes per holding. On 2026-09-27 one portfolio load with 38 such holdings sat in an
open transaction for two hours doing exactly that, and every other query in the database
queued behind an unrelated startup ``ALTER`` that could not get its lock.

The fix is cheap and the regression is expensive, so this pins the shape rather than the
plan: no live database needed, and it fails the moment someone collapses it back to an
``OR``.
"""

from __future__ import annotations

import asyncio
from datetime import date

from sqlalchemy.dialects import postgresql

# Compiling any mapped select needs every mapper registered first; importing a single
# model module is not enough (MfSipMandate -> 'User' would fail to resolve).
import app.all_models  # noqa: F401
from app.domains.portfolio.services.portfolio_service import _latest_nav_on_or_before


class _Result:
    def scalar(self):
        return None


class _CapturingSession:
    """Just enough AsyncSession to capture the statement without a database."""

    def __init__(self) -> None:
        self.statements: list = []

    async def execute(self, statement, *args, **kwargs):
        self.statements.append(statement)
        return _Result()


def _compiled_sql() -> str:
    db = _CapturingSession()
    asyncio.run(_latest_nav_on_or_before(db, "INF179K01392", date(2026, 9, 27)))
    assert len(db.statements) == 1, "one round trip, not one per key"
    return str(
        db.statements[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    ).upper()


def test_nav_lookup_uses_union_all_not_or():
    sql = _compiled_sql()
    assert "UNION ALL" in sql, "the two key lookups must be separate indexed branches"
    # "ORDER BY" does not contain " OR " (the R is followed by D), so this is specific
    # to a real disjunction.
    assert " OR " not in sql, "an OR here defeats both indexes — see the module docstring"


def test_nav_lookup_preserves_the_as_of_semantics():
    """The rewrite must return the same row: newest NAV at/behind ``on_day``."""
    sql = _compiled_sql()
    assert sql.count("NAV_DATE <=") == 2, "every branch keeps the as-of bound"
    assert sql.count("LIMIT 1") == 3, "one per branch, plus the outer pick"
    assert "DESC" in sql, "newest-first, or the outer LIMIT 1 takes the wrong row"
    assert "SCHEME_CODE =" in sql, "the AMFI-code branch"
    assert "UPPER(" in sql, "the ISIN branch, matching ix_mf_nav_isin_upper"
