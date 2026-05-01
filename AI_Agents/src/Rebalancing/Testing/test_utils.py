from __future__ import annotations

from decimal import Decimal

from Rebalancing.utils import (
    compute_exit_load,
    compute_ltcg,
    compute_stcg,
    estimate_tax,
    round_to_step,
)


def test_round_to_step_basic():
    assert round_to_step(Decimal("1234"), 100) == Decimal("1200")
    assert round_to_step(Decimal("1250"), 100) == Decimal("1300")
    assert round_to_step(Decimal("1234"), 1000) == Decimal("1000")
    assert round_to_step(Decimal(0), 100) == Decimal(0)


def test_round_to_step_negative():
    assert round_to_step(Decimal("-1234"), 100) == Decimal("-1200")


def test_round_to_step_unit_step_returns_integer():
    assert round_to_step(Decimal("12.4"), 1) == Decimal("12")
    assert round_to_step(Decimal("12.6"), 1) == Decimal("13")


def test_compute_stcg_signed():
    assert compute_stcg(Decimal("100"), Decimal("80")) == Decimal("20")
    assert compute_stcg(Decimal("80"), Decimal("100")) == Decimal("-20")


def test_compute_ltcg_signed():
    assert compute_ltcg(Decimal("500"), Decimal("400")) == Decimal("100")


def test_compute_exit_load():
    assert compute_exit_load(Decimal("10000"), 1.0) == Decimal("100")
    assert compute_exit_load(Decimal("0"), 1.0) == Decimal(0)
    assert compute_exit_load(Decimal("10000"), 0.0) == Decimal(0)


def test_estimate_tax_uses_exemption():
    # 2L LTCG with 1.25L exemption at 12.5% = 75k × 12.5% = 9375
    tax = estimate_tax(
        Decimal(0),
        Decimal("200000"),
        regime="new",
        stcg_rate_pct=20.0,
        ltcg_rate_pct=12.5,
        ltcg_exemption=Decimal("125000"),
    )
    assert tax == Decimal("9375.000")


def test_estimate_tax_losses_dont_refund():
    tax = estimate_tax(
        Decimal("-50000"),
        Decimal("-50000"),
        regime="new",
        stcg_rate_pct=20.0,
        ltcg_rate_pct=12.5,
        ltcg_exemption=Decimal("125000"),
    )
    assert tax == Decimal(0)
