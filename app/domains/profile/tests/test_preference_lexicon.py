"""Word → facet lexicon at the chat extraction boundary (S2 spec §3.2)."""

from __future__ import annotations


def _ask(target, level="more", number=None, other_words=None):
    from app.domains.profile.services.preference_lexicon import PreferenceAsk

    return PreferenceAsk(target=target, level=level, number=number, other_words=other_words)


def _build(asks):
    from app.domains.profile.services.preference_lexicon import build_intent

    return build_intent(asks)


def test_market_cap_words_map_to_beta_subgroups():
    intent, unmapped = _build([
        _ask("small_cap", "heavy"), _ask("mid_cap", "less"), _ask("large_cap", "more"),
    ])
    assert intent == {"subgroups": {
        "high_beta_equities": "heavy",
        "medium_beta_equities": "less",
        "low_beta_equities": "more",
    }}
    assert unmapped == []


def test_category_words_map_to_their_subgroups():
    intent, _ = _build([
        _ask("us_international", "none"), _ask("value", "more"), _ask("sector", "none"),
        _ask("multi_asset", "none"), _ask("short_debt", "more"), _ask("arbitrage", "heavy"),
    ])
    assert intent["subgroups"] == {
        "us_equities": "none", "value_equities": "more", "sector_equities": "none",
        "multi_asset": "none", "short_debt": "more", "arbitrage": "heavy",
    }


def test_multi_asset_accepts_only_exclusion():
    # The engine never moves multi_asset (S1 ruling 13) — a more/heavy/less on
    # it would be a silent no-op, so it is reported unmapped instead.
    for level in ("more", "heavy", "less"):
        intent, unmapped = _build([_ask("multi_asset", level)])
        assert intent == {} and unmapped == ["multi asset"], level


def test_class_words_map_to_the_asset_class_facet():
    intent, _ = _build([_ask("equity", "heavy")])
    assert intent == {"asset_class": {"class": "equity", "direction": "heavy"}}
    intent, _ = _build([_ask("debt", "none")])
    assert intent == {"asset_class": {"class": "debt", "direction": "none"}}


def test_explicit_number_on_a_class_is_a_target():
    intent, _ = _build([_ask("equity", "number", number=100)])
    assert intent == {
        "asset_class": {"class": "equity", "direction": "target", "target_pct": 100.0}
    }


def test_explicit_number_on_a_subgroup_is_stored_verbatim():
    intent, _ = _build([_ask("gold", "number", number=30)])
    assert intent == {"subgroups": {"gold_commodities": 30.0}}


def test_gold_is_emitted_as_the_subgroup_the_save_flow_routes():
    # S1 ruling 14: the save service lifts gold to the others class; the
    # lexicon does not pre-empt that (one routing rule, one place).
    intent, _ = _build([_ask("gold", "more")])
    assert intent == {"subgroups": {"gold_commodities": "more"}}


def test_multi_facet_ask_composes_class_and_subgroups():
    intent, _ = _build([_ask("equity", "more"), _ask("us_international", "none")])
    assert intent == {
        "asset_class": {"class": "equity", "direction": "more"},
        "subgroups": {"us_equities": "none"},
    }


def test_second_class_ask_is_dropped_first_wins():
    intent, _ = _build([_ask("equity", "more"), _ask("debt", "more")])
    assert intent == {"asset_class": {"class": "equity", "direction": "more"}}


def test_other_target_is_reported_unmapped_with_its_words():
    intent, unmapped = _build([_ask("other", "more", other_words="banking funds")])
    assert intent == {}
    assert unmapped == ["banking funds"]


def test_number_level_without_a_number_is_unmapped_not_invented():
    intent, unmapped = _build([_ask("equity", "number", number=None)])
    assert intent == {}
    assert unmapped == ["equity"]


def test_out_of_range_number_is_unmapped():
    intent, unmapped = _build([_ask("gold", "number", number=140)])
    assert intent == {}
    assert unmapped == ["gold"]
    intent, unmapped = _build([_ask("small_cap", "number", number=-5)])
    assert intent == {}
    assert unmapped == ["small cap"]


def test_empty_or_none_asks_build_empty_intent():
    assert _build(None) == ({}, [])
    assert _build([]) == ({}, [])


def test_every_lexicon_target_is_a_settable_engine_key():
    """Every subgroup the lexicon emits must be one the engine accepts (never a
    frozen row); every class must be an engine asset class."""
    from app.domains.ai_engine.common import ensure_ai_agents_path

    ensure_ai_agents_path()
    from practical_asset_allocation.human_override import (
        ASSET_CLASSES, FROZEN_SUBGROUPS, SETTABLE_SUBGROUPS,
    )
    from app.domains.profile.services.preference_lexicon import TARGET_TO_FACET

    for kind, key in TARGET_TO_FACET.values():
        if kind == "subgroup":
            assert key in SETTABLE_SUBGROUPS and key not in FROZEN_SUBGROUPS, key
        else:
            assert key in ASSET_CLASSES, key
