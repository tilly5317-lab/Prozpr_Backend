"""Unit tests for the portfolio_query prompt-size guards (audit F6).

Covers the top-N holdings itemization + full-portfolio count metadata in
_build_portfolio_context, and the compact enriched-JSON dump on the agent side.
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from app.domains.portfolio.services.portfolio_query_service import (
    _MAX_ITEMIZED_HOLDINGS,
    _build_portfolio_context,
)
from portfolio_query.orchestrator import _enrich_inr_fields
from portfolio_query.models import PortfolioContext


def _orm_holding(name: str, value: float, itype: str = "mutual_fund") -> SimpleNamespace:
    return SimpleNamespace(
        instrument_name=name,
        ticker_symbol=None,
        instrument_type=itype,
        quantity=10.0,
        average_cost=value / 10.0 / 1.2,  # gives a computable cost basis
        current_value=value,
        current_price=None,
        allocation_percentage=None,
        return_1y=None,
        return_3y=None,
        fund_metadata=SimpleNamespace(category="Equity", sub_category="Mid Cap Fund"),
    )


def _orm_user(holdings: list[SimpleNamespace]) -> SimpleNamespace:
    total = sum(h.current_value for h in holdings)
    portfolio = SimpleNamespace(
        is_primary=True,
        holdings=holdings,
        allocations=[],
        total_value=total,
        total_invested=None,
        total_gain_percentage=None,
    )
    return SimpleNamespace(portfolios=[portfolio], mf_transactions=[])


class ItemizeHoldingsTests(unittest.TestCase):
    def test_small_portfolio_fully_itemized_no_omission_metadata(self):
        user = _orm_user([_orm_holding(f"Fund {i}", 1000.0 * (i + 1)) for i in range(5)])

        ctx = _build_portfolio_context(user)

        self.assertEqual(len(ctx.holdings), 5)
        self.assertEqual(ctx.total_holdings_count, 5)
        self.assertEqual(ctx.holdings_count_by_type, {"mutual_fund": 5})
        self.assertIsNone(ctx.omitted_holdings_count)
        self.assertIsNone(ctx.omitted_holdings_value_inr)

    def test_large_portfolio_caps_itemization_and_rolls_up_tail(self):
        n = _MAX_ITEMIZED_HOLDINGS + 15
        holdings = [
            _orm_holding(f"Fund {i}", 1000.0 * (i + 1), "mutual_fund" if i % 3 else "equity")
            for i in range(n)
        ]
        user = _orm_user(holdings)

        ctx = _build_portfolio_context(user)

        self.assertEqual(len(ctx.holdings), _MAX_ITEMIZED_HOLDINGS)
        # Largest first; the itemized slice is the top of the ranking.
        values = [h.current_value_inr for h in ctx.holdings]
        self.assertEqual(values, sorted(values, reverse=True))
        self.assertEqual(values[0], 1000.0 * n)
        # Counts cover the FULL portfolio, not the slice.
        self.assertEqual(ctx.total_holdings_count, n)
        self.assertEqual(sum(ctx.holdings_count_by_type.values()), n)
        # Tail rollup: the 15 smallest = 1000 * (1..15).
        self.assertEqual(ctx.omitted_holdings_count, 15)
        self.assertEqual(ctx.omitted_holdings_value_inr, 1000.0 * sum(range(1, 16)))
        # Sub-category rollups still aggregate the full portfolio.
        total_subcat = sum(r.amount_inr for r in ctx.sub_category_allocations)
        self.assertEqual(total_subcat, 1000.0 * sum(range(1, n + 1)))


def _orm_fof_holding(value: float = 1000.0) -> SimpleNamespace:
    # An equity-index FoF whose raw AMFI `category` is the stale/coarse label:
    # the canonical classifier must look through the sub_category + name to Equity.
    return SimpleNamespace(
        instrument_name="Groww Nifty India Internet ETF FOF",
        ticker_symbol=None,
        instrument_type="mutual_fund",
        quantity=10.0,
        average_cost=value / 10.0 / 1.2,
        current_value=value,
        current_price=None,
        allocation_percentage=None,
        return_1y=None,
        return_3y=None,
        fund_metadata=SimpleNamespace(category="Others", sub_category="FoF Domestic"),
    )


class CanonicalClassificationTests(unittest.TestCase):
    def test_holding_asset_class_uses_canonical_classifier_not_raw_category(self):
        # Old path read md.category ("Others"); canonical looks through to Equity.
        ctx = _build_portfolio_context(_orm_user([_orm_fof_holding()]))
        self.assertEqual(ctx.holdings[0].asset_class, "Equity")

    def test_allocations_derived_from_holdings_ignore_stale_stored_rows(self):
        user = _orm_user([_orm_fof_holding(1000.0)])
        # Stale persisted allocation says 100% Others — must be ignored.
        user.portfolios[0].allocations = [
            SimpleNamespace(asset_class="Others", allocation_percentage=100.0, amount=1000.0)
        ]
        ctx = _build_portfolio_context(user)
        by = {r.asset_class: r for r in ctx.allocations}
        self.assertIn("Equity", by)
        self.assertAlmostEqual(by["Equity"].percentage, 100.0)
        self.assertNotIn("Others", by)

    def test_allocations_carry_forward_cash_from_persisted(self):
        user = _orm_user([_orm_fof_holding(900.0)])
        user.portfolios[0].total_value = 1000.0  # 900 in holdings + 100 cash
        user.portfolios[0].allocations = [
            SimpleNamespace(asset_class="Cash", allocation_percentage=10.0, amount=100.0)
        ]
        ctx = _build_portfolio_context(user)
        by = {r.asset_class: r for r in ctx.allocations}
        self.assertIn("Equity", by)
        self.assertIn("Cash", by)
        self.assertAlmostEqual(by["Cash"].amount_inr, 100.0)


class CompactDumpTests(unittest.TestCase):
    def test_dump_is_compact_excludes_none_and_enriches_inr(self):
        ctx = PortfolioContext(
            total_value_inr=25_000_000.0,
            total_invested_inr=None,
            holdings=[],
        )

        # The formatter dumps the facts pack compactly (separators + literal ₹);
        # this asserts the enrichment and exclude_none that feed it.
        enriched = _enrich_inr_fields(ctx.model_dump(exclude_none=True))
        dumped = json.dumps(enriched, separators=(",", ":"), ensure_ascii=False)

        self.assertNotIn("\n", dumped)  # no pretty-printing
        self.assertNotIn('": ', dumped)  # compact separators
        self.assertNotIn("total_invested_inr", dumped)  # None excluded
        self.assertIn('"total_value_indian":"₹2.5 crore"', dumped)


if __name__ == "__main__":
    unittest.main()
