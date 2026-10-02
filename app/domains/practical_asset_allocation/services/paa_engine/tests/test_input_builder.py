"""CorpusPin threading through the practical-allocation input builder.

Tests at the CAPTURE SEAM: ``PracticalAllocationInput`` is monkeypatched inside
the builder module with a kwargs recorder, so no fully-valid allocation input
has to be fabricated and no validation runs (fakes only — house style)."""

import types

import pytest

from app.domains.practical_asset_allocation.services.paa_engine import input_builder


def _fake_base(total_corpus: float):
    """Every shared AllocationInput field present (the builder getattr's each of
    model_fields); values are irrelevant — PracticalAllocationInput is replaced
    by a kwargs recorder below, so nothing validates them."""
    from asset_allocation_pydantic.models import AllocationInput

    base = types.SimpleNamespace(**{k: 0.0 for k in AllocationInput.model_fields})
    base.total_corpus = total_corpus
    return base


@pytest.fixture
def captured(monkeypatch):
    """Replace PracticalAllocationInput (in the builder module) with a recorder.
    Sets attributes too, because the builder's debug dict reads them back."""
    seen = {}

    class _Capture:
        def __init__(self, **kw):
            seen.update(kw)
            self.__dict__.update(kw)

    monkeypatch.setattr(input_builder, "PracticalAllocationInput", _Capture)
    return seen


def test_corpus_pin_overrides_both_scalars(monkeypatch, captured):
    monkeypatch.setattr(
        input_builder, "build_goal_allocation_input_for_user",
        lambda ctx: (_fake_base(999999.0), {}),   # profile figure — must lose
    )
    pin = input_builder.CorpusPin(
        total_corpus=550000.0, elss_corpus=20000.0,
    )
    input_builder.build_practical_allocation_input_for_user(None, corpus_pin=pin)
    assert captured["total_corpus"] == 550000.0
    assert captured["elss_corpus"] == 20000.0


def test_no_pin_keeps_profile_defaults(monkeypatch, captured):
    monkeypatch.setattr(
        input_builder, "build_goal_allocation_input_for_user",
        lambda ctx: (_fake_base(999999.0), {}),
    )
    input_builder.build_practical_allocation_input_for_user(None)
    assert captured["total_corpus"] == 999999.0
    assert captured["elss_corpus"] == 0.0


def _holding(sub_category, value, scheme_name="X Fund"):
    return types.SimpleNamespace(
        instrument_type="mutual_fund", current_value=value, instrument_name=scheme_name,
        fund_metadata=types.SimpleNamespace(sub_category=sub_category, scheme_name=scheme_name),
    )


def test_short_term_holdings_count_debt_and_arbitrage_not_income_plus_arbitrage():
    user = types.SimpleNamespace(portfolios=[types.SimpleNamespace(holdings=[
        _holding("Liquid Fund", 300_000.0),
        _holding("Arbitrage Fund", 200_000.0),
        _holding("Gilt Fund", 100_000.0),
        _holding("Large Cap Fund", 900_000.0),
        _holding("FoF Domestic", 400_000.0, scheme_name="ICICI Income plus Arbitrage FoF"),
    ])])
    assert input_builder.short_term_holdings_for_user(user) == 600_000.0


def test_no_holdings_on_file_is_none():
    assert input_builder.short_term_holdings_for_user(types.SimpleNamespace(portfolios=[])) is None
    assert input_builder.short_term_holdings_for_user(None) is None


def test_builder_sets_holdings_and_the_sip_override(monkeypatch, captured):
    monkeypatch.setattr(
        input_builder, "build_goal_allocation_input_for_user",
        lambda ctx: (_fake_base(1_000_000.0), {}),
    )
    monkeypatch.setattr(input_builder, "short_term_holdings_for_user", lambda user: 250_000.0)
    input_builder.build_practical_allocation_input_for_user(None, monthly_sip=40_000.0)
    assert captured["short_term_holdings"] == 250_000.0
    assert captured["monthly_sip"] == 40_000.0


def test_builder_keeps_the_profile_sip_without_an_override(monkeypatch, captured):
    base = _fake_base(1_000_000.0)
    base.monthly_sip = 15_000.0
    monkeypatch.setattr(input_builder, "build_goal_allocation_input_for_user", lambda ctx: (base, {}))
    input_builder.build_practical_allocation_input_for_user(None)
    assert captured["monthly_sip"] == 15_000.0


def test_explicit_holdings_skip_the_user_read(monkeypatch, captured):
    monkeypatch.setattr(
        input_builder, "build_goal_allocation_input_for_user",
        lambda ctx: (_fake_base(1_000_000.0), {}),
    )

    def _boom(user):
        raise AssertionError("must not read the user's holdings")

    monkeypatch.setattr(input_builder, "short_term_holdings_for_user", _boom)
    input_builder.build_practical_allocation_input_for_user(None, short_term_holdings=123_000.0)
    assert captured["short_term_holdings"] == 123_000.0
