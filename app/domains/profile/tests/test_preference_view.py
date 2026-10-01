"""preference_view: one reader for a saved preference row, whichever surface wrote it."""

from __future__ import annotations

from types import SimpleNamespace

from app.domains.mutual_funds.services.investment_preferences import ResolvedPreferences
from app.domains.profile.services.preference_save_service import (
    _changed_intent,
    _merge_field_level,
    _rekey_arbitrage_to_held_twin,
    one_off_override,
)
from app.domains.profile.services.preference_view import (
    active_preferences_block,
    active_preferences_for,
    canonical_intent,
    describe,
)


def _row(customer_choices, *, mix=None, targets=None):
    """A SavedInvestmentPreference stand-in. `asset_class_requested` is a
    property on the real model over the three *_requested_pct columns."""
    return SimpleNamespace(
        customer_choices=customer_choices,
        asset_class_requested=mix,
        resolved_targets=targets,
    )


def test_none_row_is_empty():
    assert canonical_intent(None) == {}


def test_chat_shaped_row_passes_through_untouched():
    """Relative tokens MUST survive — the caller's anti-ratchet guard compares
    them, and turning 'more' into a number re-resolves on every repeat."""
    choices = {
        "asset_class": {"class": "equity", "direction": "more"},
        "subgroups": {"gold_commodities": "more"},
    }
    row = _row(
        choices,
        mix={"equity": 70.0, "debt": 20.0, "others": 10.0},
        targets={"gold_commodities": 12.0},
    )

    assert canonical_intent(row) == choices


def test_screen_shaped_row_derives_from_typed_columns():
    """The screen's own payload is NOT translated: resolved_targets is what the
    engine consumed, so the chat merge composes over exactly that."""
    row = _row(
        {
            "class_mix": {"equity": 60.0, "debt": 30.0, "others": 10.0},
            "pins": [{"subgroup": "low_beta_equities", "pct_of_total": 30.0}],
        },
        mix={"equity": 60.0, "debt": 30.0, "others": 10.0},
        targets={"low_beta_equities": 30.0, "high_beta_equities": 0.0},
    )

    assert canonical_intent(row) == {
        "asset_class": {"equity": 60.0, "debt": 30.0, "others": 10.0},
        "subgroups": {"low_beta_equities": 30.0, "high_beta_equities": 0.0},
    }


def test_zero_pin_survives_as_an_exclusion():
    """0 is a hard exclusion the engine honours; an OMITTED subgroup means
    'engine decides'. Collapsing the two silently un-excludes a category."""
    row = _row(
        {"class_mix": {}, "pins": []}, mix=None, targets={"high_beta_equities": 0.0}
    )

    assert canonical_intent(row)["subgroups"] == {"high_beta_equities": 0.0}


def test_unrecognised_shape_falls_back_to_typed_columns():
    row = _row(
        {"something_new": 1},
        mix={"equity": 100.0, "debt": 0.0, "others": 0.0},
        targets={},
    )

    assert canonical_intent(row) == {
        "asset_class": {"equity": 100.0, "debt": 0.0, "others": 0.0}
    }


def test_empty_row_yields_empty_intent():
    assert canonical_intent(_row(None, mix=None, targets=None)) == {}


# ---------------------------------------------------------------------------
# Composition over a saved row (the Phase 1 bug)
# ---------------------------------------------------------------------------


def test_chat_ask_composes_over_a_screen_saved_row():
    """THE BUG (spec problem 2): a category ask over a screen-saved
    distribution used to send the engine that ONE category — class mix and
    every other pin discarded. It must now compose over them."""
    row = _row(
        {
            "class_mix": {"equity": 60.0, "debt": 30.0, "others": 10.0},
            "pins": [
                {"subgroup": "low_beta_equities", "pct_of_total": 30.0},
                {"subgroup": "high_beta_equities", "pct_of_total": 0.0},
            ],
        },
        mix={"equity": 60.0, "debt": 30.0, "others": 10.0},
        targets={"low_beta_equities": 30.0, "high_beta_equities": 0.0},
    )
    stored = canonical_intent(row)
    # A subgroup-level ask. NOT gold: `_route_sole_class_subgroups` rewrites a
    # sole-class subgroup like gold into a CLASS facet, a different path.
    ask = {"subgroups": {"high_beta_equities": "more"}}

    # resolve_one_off's merge step, verbatim (its DB half is exercised in the
    # live harness; this is the pure composition the bug lived in).
    intent = {}
    asset_class = ask.get("asset_class", stored.get("asset_class"))
    if asset_class:
        intent["asset_class"] = asset_class
    subgroups = {**(stored.get("subgroups") or {}), **(ask.get("subgroups") or {})}
    if subgroups:
        intent["subgroups"] = subgroups

    changed = _changed_intent(intent, stored)
    assert changed == {"subgroups": {"high_beta_equities": "more"}}, (
        "only the asked-for subgroup re-resolves; everything else reuses its "
        "stored value (the anti-ratchet guard)"
    )
    resolved_changed = ResolvedPreferences(
        asset_class_requested=None,
        subgroup_emphasis={"high_beta_equities": 8.0},
        applied_defaults={},
    )
    one_off = one_off_override(
        _merge_field_level(intent, row, resolved_changed, changed)
    )

    # The screen's OTHER pin survives at its stored value — that is the bug.
    assert one_off["subgroup_emphasis"] == {
        "low_beta_equities": 30.0,
        "high_beta_equities": 8.0,
    }
    # And the class mix comes back, carried explicitly by canonical_intent ->
    # _merge_field_level's row fallback (NOT by the None-omission below: with a
    # class facet in `intent` that branch never produces a None).
    assert one_off["asset_class_requested"] == {
        "equity": 60.0,
        "debt": 30.0,
        "others": 10.0,
    }


def test_a_null_class_facet_is_omitted_not_sent_as_none():
    """The other fix, in the scenario that actually triggers it: a CHAT-shaped
    row carries no class facet, so `_merge_field_level` yields None — and a
    None in the one-off deletes the saved typed-column mix."""
    row = _row(
        {"subgroups": {"gold_commodities": "more"}},
        mix={"equity": 60.0, "debt": 30.0, "others": 10.0},
        targets={"gold_commodities": 12.0},
    )
    stored = canonical_intent(row)
    assert "asset_class" not in stored, "a chat-shaped row passes through as-is"

    intent = {"subgroups": {"gold_commodities": "heavy"}}
    changed = _changed_intent(intent, stored)
    resolved_changed = ResolvedPreferences(
        asset_class_requested=None,
        subgroup_emphasis={"gold_commodities": 20.0},
        applied_defaults={},
    )
    one_off = one_off_override(
        _merge_field_level(intent, row, resolved_changed, changed)
    )

    assert "asset_class_requested" not in one_off, (
        "a null class facet must be OMITTED, not sent as None — None shadows "
        "the saved mix in {**saved, **one_off} and is then filtered out"
    )
    saved_fields = {
        "asset_class_requested": row.asset_class_requested,
        "subgroup_emphasis": row.resolved_targets,
    }
    merged = {
        k: v
        for k, v in {**saved_fields, **one_off}.items()
        if v not in (None, [], {})
    }
    assert merged["asset_class_requested"] == {
        "equity": 60.0,
        "debt": 30.0,
        "others": 10.0,
    }


def test_the_arbitrage_rekey_keeps_the_other_twin_s_stored_pin():
    """Both twins are settable, so a screen distribution pins both. Re-keying
    the ask onto the held twin must not drop the other twin's exclusion."""
    row = _row(
        {"class_mix": {}, "pins": []},
        mix=None,
        targets={"arbitrage": 0.0, "arbitrage_plus_income": 10.0},
    )
    stored = canonical_intent(row)
    intent = {"subgroups": {**stored["subgroups"], "arbitrage": "more"}}

    rekeyed = _rekey_arbitrage_to_held_twin(intent, stored)

    assert rekeyed["subgroups"]["arbitrage_plus_income"] == "more"
    assert rekeyed["subgroups"]["arbitrage"] == 0.0, (
        "the stored exclusion on the other twin must survive the re-key"
    )


# ---------------------------------------------------------------------------
# describe / the facts-pack block
# ---------------------------------------------------------------------------


def _practical(applied=True, shortfall=None):
    return SimpleNamespace(
        human_override_applied=SimpleNamespace(
            preference_applied=applied, shortfall_reason=shortfall
        )
    )


def test_describe_renders_class_mix_and_categories_in_customer_words():
    row = _row(
        {"class_mix": {}, "pins": []},
        mix={"equity": 60.0, "debt": 30.0, "others": 10.0},
        targets={"low_beta_equities": 30.0, "high_beta_equities": 0.0},
    )

    choices = describe(row)

    # "others" is the ENGINE's word; the customer's is Commodity (the screen's
    # own validation says "Equity + Debt + Commodity must total 100%").
    assert choices[0] == "60% equity / 30% debt / 10% commodity"
    assert "others" not in choices[0]
    assert "nothing in small-cap equity" in choices
    # resolved_targets is a share of the WHOLE portfolio — the phrase must say
    # so, or 30% beside an equity figure reads as 30% OF equity.
    assert "30% of your portfolio in large-cap equity" in choices
    # No engine keys may reach a customer-facing string.
    assert not any("high_beta" in c or "low_beta" in c for c in choices)


def test_describe_caps_the_category_list():
    targets = {
        "low_beta_equities": 20.0,
        "medium_beta_equities": 15.0,
        "high_beta_equities": 0.0,
        "value_equities": 10.0,
        "sector_equities": 0.0,
        "short_debt": 25.0,
    }
    row = _row({"class_mix": {}, "pins": []}, mix=None, targets=targets)

    choices = describe(row)

    assert len(choices) <= 3, "a chat sentence cannot carry twelve categories"
    # The pins the customer SET lead; every blank row costs one phrase between
    # them (see the complete-distribution test below).
    assert choices[0].startswith("25% of your portfolio in")
    assert sum(1 for c in choices if c.startswith("nothing in")) == 1


def test_describe_is_none_when_there_is_nothing_to_say():
    assert describe(None) is None
    assert describe(_row(None, mix=None, targets=None)) is None


def test_block_carries_applied_and_shortfall_from_the_run():
    row = _row(
        {"class_mix": {}, "pins": []},
        mix={"equity": 60.0, "debt": 30.0, "others": 10.0},
        targets={},
    )

    block = active_preferences_block(row, _practical(shortfall="elss_frozen"))

    assert block["applied"] is True
    assert block["shortfall_reason"] == "elss_frozen"
    assert block["choices"][0] == "60% equity / 30% debt / 10% commodity"


def test_block_is_none_when_the_run_applied_no_override():
    row = _row({"class_mix": {}, "pins": []}, mix={"equity": 60.0}, targets={})

    assert (
        active_preferences_block(row, SimpleNamespace(human_override_applied=None))
        is None
    )
    assert active_preferences_block(row, _practical(applied=False)) is None
    assert active_preferences_block(None, _practical()) is None


def test_block_is_none_for_an_output_without_the_field():
    """AA falls back to displaying the preference-free IDEAL output, whose model
    has no human_override_applied at all. It must stay silent, not assert."""
    row = _row({"class_mix": {}, "pins": []}, mix={"equity": 60.0}, targets={})

    assert active_preferences_block(row, SimpleNamespace()) is None


# ---------------------------------------------------------------------------
# The cross-domain accessors (other domains must not read the relationship)
# ---------------------------------------------------------------------------


def test_the_user_level_accessor_reads_the_relationship_here():
    """`test_contract_single_computation_reader` reserves reads of
    `user.saved_investment_preference` for sanctioned modules; chat modules call
    this instead. (`describe_active` was removed 2026-09-17 — with the readout
    retired, the disclosure block is the only user-level consumer left.)"""
    row = _row(
        {"class_mix": {}, "pins": []},
        mix={"equity": 60.0, "debt": 30.0, "others": 10.0},
        targets={},
    )
    user = SimpleNamespace(saved_investment_preference=row)

    assert active_preferences_for(user, _practical())["applied"] is True

    no_pref = SimpleNamespace(saved_investment_preference=None)
    assert active_preferences_for(no_pref, _practical()) is None


def test_a_complete_screen_distribution_leads_with_what_the_customer_SET():
    """Review finding (2026-09-17, major): the 3-phrase cap was sized for the
    chat era (one or two exclusions). The screen now writes a COMPLETE
    distribution — 11 settable categories, so a normal save carries 5-8 explicit
    zeros — and exclusions were built first, so they filled every slot and every
    pin the customer actually set was dropped. The prompt then quotes this
    verbatim as "the preferences you saved"."""
    row = _row(
        {"class_mix": {}, "pins": []},
        mix={"equity": 65.0, "debt": 25.0, "others": 10.0},
        targets={
            "low_beta_equities": 35.0,
            "medium_beta_equities": 20.0,
            "arbitrage_plus_income": 25.0,
            "us_equities": 10.0,
            "gold_commodities": 10.0,
            # the six they left blank — explicit zeros from the screen
            "high_beta_equities": 0.0,
            "value_equities": 0.0,
            "sector_equities": 0.0,
            "multi_asset": 0.0,
            "short_debt": 0.0,
            "arbitrage": 0.0,
        },
    )

    choices = describe(row)
    blob = " | ".join(choices)

    assert choices[0] == "65% equity / 25% debt / 10% commodity"
    # The largest thing they chose must be present — it was being dropped.
    assert "35% of your portfolio in large-cap equity" in choices, (
        f"the biggest pin the customer set is missing: {choices}"
    )
    assert "25% of your portfolio in arbitrage & income" in choices
    # Exclusions may appear, but must not crowd out the pins: they cost ONE slot.
    assert sum(1 for c in choices if c.startswith("nothing in")) <= 1
    assert len(choices) <= 4, f"too long for one chat sentence: {choices}"
    assert "high_beta" not in blob and "beta_equities" not in blob


def test_exclusions_are_collapsed_into_one_phrase():
    row = _row(
        {"class_mix": {}, "pins": []},
        mix=None,
        targets={
            "high_beta_equities": 0.0,
            "sector_equities": 0.0,
            "us_equities": 0.0,
            "multi_asset": 0.0,
        },
    )

    choices = describe(row)

    assert len(choices) == 1, f"four exclusions must cost one phrase: {choices}"
    assert choices[0].startswith("nothing in ")
    assert "small-cap equity" in choices[0]


def test_an_exclusion_only_preference_still_says_something():
    row = _row({"class_mix": {}, "pins": []}, mix=None,
               targets={"high_beta_equities": 0.0})

    assert describe(row) == ["nothing in small-cap equity"]
