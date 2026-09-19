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


def test_corpus_pin_overrides_all_four_scalars(monkeypatch, captured):
    monkeypatch.setattr(
        input_builder, "build_goal_allocation_input_for_user",
        lambda ctx: (_fake_base(999999.0), {}),   # profile figure — must lose
    )
    pin = input_builder.CorpusPin(
        total_corpus=550000.0, mf_corpus=500000.0,
        non_mf_equity_corpus=30000.0, elss_corpus=20000.0,
    )
    input_builder.build_practical_allocation_input_for_user(None, corpus_pin=pin)
    assert captured["total_corpus"] == 550000.0
    assert captured["mf_corpus"] == 500000.0
    assert captured["non_mf_equity_corpus"] == 30000.0
    assert captured["elss_corpus"] == 20000.0


def test_no_pin_keeps_profile_defaults(monkeypatch, captured):
    monkeypatch.setattr(
        input_builder, "build_goal_allocation_input_for_user",
        lambda ctx: (_fake_base(999999.0), {}),
    )
    input_builder.build_practical_allocation_input_for_user(None)
    assert captured["total_corpus"] == 999999.0
    assert captured["mf_corpus"] == 999999.0
    assert captured["non_mf_equity_corpus"] == 0.0
    assert captured["elss_corpus"] == 0.0
