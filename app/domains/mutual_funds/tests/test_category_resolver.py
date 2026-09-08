"""Tests for the shared free-text → canonical sub_category resolver."""

from __future__ import annotations

from app.domains.mutual_funds.services.category_resolver import (
    resolve_categories,
    resolve_category,
)


def test_resolves_common_phrasings_to_canonical_sub_categories():
    resolved, unresolved = resolve_categories(["large cap", "midcap"])
    assert resolved == ["Large Cap Fund", "Mid Cap Fund"]
    assert unresolved == []


def test_unknown_word_reported_not_guessed():
    resolved, unresolved = resolve_categories(["crypto"])
    assert resolved == [] and unresolved == ["crypto"]


def test_single_resolve_matches_moved_behavior():
    assert resolve_category("bluechip") == "Large Cap Fund"
    assert resolve_category("") is None
    assert resolve_category(None) is None


def test_dedupes_and_preserves_order():
    resolved, unresolved = resolve_categories(["largecap", "large cap", "small cap"])
    assert resolved == ["Large Cap Fund", "Small Cap Fund"]   # no dupe
    assert unresolved == []
