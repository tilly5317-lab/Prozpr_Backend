"""Unit tests for the canonical scheme-code resolver (pure-function logic)."""

from __future__ import annotations

from app.domains.mutual_funds.services.scheme_resolver import (
    canonical_scheme_code,
    is_amfi_code,
)

_MAP = {"INF666M01LC7": "153626", "INF179K01426": "101764"}


def test_is_amfi_code_numeric_only():
    assert is_amfi_code("152712")
    assert not is_amfi_code("INF666M01LC7")  # ISIN
    assert not is_amfi_code("GFD")  # RTA code
    assert not is_amfi_code("")
    assert not is_amfi_code(None)


def test_numeric_amfi_passes_through():
    # A real AMFI code is kept even if an (irrelevant) ISIN is also present.
    assert (
        canonical_scheme_code(amfi="152712", isin="INF666M01LC7", isin_to_amfi=_MAP)
        == "152712"
    )


def test_isin_resolves_to_amfi():
    assert (
        canonical_scheme_code(amfi=None, isin="INF666M01LC7", isin_to_amfi=_MAP)
        == "153626"
    )
    # case-insensitive
    assert (
        canonical_scheme_code(amfi=None, isin="inf666m01lc7", isin_to_amfi=_MAP)
        == "153626"
    )


def test_isin_in_amfi_slot_resolves():
    # CAS ingest stores amfi-or-isin in the amfi slot; an ISIN there still resolves.
    assert (
        canonical_scheme_code(amfi="INF179K01426", isin=None, isin_to_amfi=_MAP)
        == "101764"
    )


def test_unresolvable_is_preserved_not_dropped():
    # Bare RTA code with no mapping → kept as-is (nothing lost).
    assert canonical_scheme_code(amfi="GFD", isin=None, isin_to_amfi=_MAP) == "GFD"
    # Unknown ISIN → kept as-is.
    assert (
        canonical_scheme_code(amfi=None, isin="INF000000000", isin_to_amfi=_MAP)
        == "INF000000000"
    )


def test_nothing_present_returns_none():
    assert canonical_scheme_code(amfi=None, isin=None, isin_to_amfi=_MAP) is None
