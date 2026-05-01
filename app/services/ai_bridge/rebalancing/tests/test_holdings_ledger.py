"""Lot-level holdings ledger built from MfTransaction rows, FIFO-consumed by sells."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.services.ai_bridge.rebalancing.holdings_ledger import (
    HoldingLedgerEntry,
    Lot,
    build_holdings_ledger,
)


@pytest.mark.asyncio
async def test_buy_only_yields_one_entry(
    db_session, fixture_user, fixture_buy_txn_factory, fixture_nav_isin_factory,
):
    await fixture_buy_txn_factory(
        user=fixture_user, scheme_code="100001",
        units=Decimal("10"), nav=Decimal("50"), txn_date=date(2025, 1, 1),
    )
    await fixture_nav_isin_factory(scheme_code="100001", isin="INF000000001")

    ledger = await build_holdings_ledger(db_session, user_id=fixture_user.id)

    assert len(ledger) == 1
    entry = ledger[0]
    assert isinstance(entry, HoldingLedgerEntry)
    assert entry.isin == "INF000000001"
    assert entry.scheme_code == "100001"
    assert len(entry.lots) == 1
    assert isinstance(entry.lots[0], Lot)
    assert entry.lots[0].units == Decimal("10")
    assert entry.lots[0].acquisition_nav == Decimal("50")
    assert entry.lots[0].acquisition_date == date(2025, 1, 1)


@pytest.mark.asyncio
async def test_sell_consumes_oldest_lot_fifo(
    db_session, fixture_user, fixture_buy_txn_factory, fixture_sell_txn_factory, fixture_nav_isin_factory,
):
    await fixture_buy_txn_factory(user=fixture_user, scheme_code="100001",
                                  units=Decimal("10"), nav=Decimal("50"), txn_date=date(2025, 1, 1))
    await fixture_buy_txn_factory(user=fixture_user, scheme_code="100001",
                                  units=Decimal("5"), nav=Decimal("60"), txn_date=date(2025, 6, 1))
    await fixture_sell_txn_factory(user=fixture_user, scheme_code="100001",
                                   units=Decimal("8"), nav=Decimal("70"), txn_date=date(2025, 9, 1))
    await fixture_nav_isin_factory(scheme_code="100001", isin="INF000000001")

    ledger = await build_holdings_ledger(db_session, user_id=fixture_user.id)

    entry = ledger[0]
    assert len(entry.lots) == 2
    # Oldest lot consumed: 10-8 = 2 units left from Jan 1
    assert entry.lots[0].acquisition_date == date(2025, 1, 1)
    assert entry.lots[0].units == Decimal("2")
    # Jun 1 lot untouched
    assert entry.lots[1].acquisition_date == date(2025, 6, 1)
    assert entry.lots[1].units == Decimal("5")


@pytest.mark.asyncio
async def test_fully_sold_position_dropped(
    db_session, fixture_user, fixture_buy_txn_factory, fixture_sell_txn_factory, fixture_nav_isin_factory,
):
    await fixture_buy_txn_factory(user=fixture_user, scheme_code="100001",
                                  units=Decimal("10"), nav=Decimal("50"), txn_date=date(2025, 1, 1))
    await fixture_sell_txn_factory(user=fixture_user, scheme_code="100001",
                                   units=Decimal("10"), nav=Decimal("60"), txn_date=date(2025, 6, 1))
    await fixture_nav_isin_factory(scheme_code="100001", isin="INF000000001")

    ledger = await build_holdings_ledger(db_session, user_id=fixture_user.id)
    assert ledger == []


@pytest.mark.asyncio
async def test_sell_consuming_partial_first_lot_then_into_second(
    db_session, fixture_user, fixture_buy_txn_factory, fixture_sell_txn_factory, fixture_nav_isin_factory,
):
    """Sell of 12 units against lots of 10 + 5 leaves 3 in the second lot."""
    await fixture_buy_txn_factory(user=fixture_user, scheme_code="100001",
                                  units=Decimal("10"), nav=Decimal("50"), txn_date=date(2025, 1, 1))
    await fixture_buy_txn_factory(user=fixture_user, scheme_code="100001",
                                  units=Decimal("5"), nav=Decimal("60"), txn_date=date(2025, 6, 1))
    await fixture_sell_txn_factory(user=fixture_user, scheme_code="100001",
                                   units=Decimal("12"), nav=Decimal("70"), txn_date=date(2025, 9, 1))
    await fixture_nav_isin_factory(scheme_code="100001", isin="INF000000001")

    ledger = await build_holdings_ledger(db_session, user_id=fixture_user.id)

    entry = ledger[0]
    assert len(entry.lots) == 1
    assert entry.lots[0].acquisition_date == date(2025, 6, 1)
    assert entry.lots[0].units == Decimal("3")
    assert entry.lots[0].acquisition_nav == Decimal("60")
