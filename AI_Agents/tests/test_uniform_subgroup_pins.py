"""Uniform sub-group pins (spec 2026-09-14 §4.2/§4.3/§5): every customer
sub-group number is an INPUT at its deciding phase, and bounds the sleeve.
Step 6 no longer reshapes anything."""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from test_human_override_golden import (  # noqa: E402
    make_practical_input,
    trim_disclosure,
)

POOL = 10_000_000


def _phase5(**kw):
    from asset_allocation_pydantic.models import MarketCommentaryScores
    from asset_allocation_pydantic.steps.step4_long_term import phase5_equity_subgroups

    return phase5_equity_subgroups(
        total_equity_for_subgroups=kw.pop("pool", POOL),
        score=kw.pop("score", 7.0),
        market_commentary=kw.pop("mc", MarketCommentaryScores()),
        **kw,
    )


class TestPhase5Placement:
    def test_no_request_is_unchanged(self):
        assert _phase5() == _phase5(requested_amounts=None, excluded=None)

    def test_a_pin_is_placed_verbatim(self):
        assert _phase5(requested_amounts={"low_beta_equities": 4_000_000})["low_beta_equities"] == 4_000_000

    def test_the_pool_is_still_fully_spent(self):
        out = _phase5(requested_amounts={"low_beta_equities": 4_000_000})
        assert sum(out.values()) == POOL

    def test_an_exclusion_is_never_refilled(self):
        out = _phase5(excluded=frozenset({"us_equities"}))
        assert out["us_equities"] == 0
        assert sum(out.values()) == POOL

    def test_pin_and_exclusion_compose(self):
        out = _phase5(
            requested_amounts={"low_beta_equities": 3_000_000},
            excluded=frozenset({"us_equities"}),
        )
        assert out["low_beta_equities"] == 3_000_000 and out["us_equities"] == 0
        assert sum(out.values()) == POOL

    def test_pins_exceeding_the_pool_raise(self):
        import pytest
        with pytest.raises(ValueError):
            _phase5(requested_amounts={"low_beta_equities": POOL + 1})


def _slider(amounts, **kw):
    from asset_allocation_pydantic.equity_subgroup_slider import (
        apply_equity_subgroup_slider,
    )

    return apply_equity_subgroup_slider(
        amounts,
        equity_pool=kw.pop("pool", POOL),
        **kw,
    )


class TestSliderExemption:
    """D-A1 — the slider exists to stop the ENGINE producing dust. A number the
    customer typed is not the engine's to police, so pins are exempt: never
    dropped, never rescaled, and out of the redistribution base."""

    # sector_equities is 5% of the pool — below the flat 8% threshold an
    # ideal-engine call produces (locked_amount = 0 → first_term = 8).
    PIN = {
        "sector_equities": 500_000,
        "medium_beta_equities": 5_500_000,
        "high_beta_equities": 4_000_000,
    }

    def test_an_exempt_pin_survives(self):
        out, _min_pct, _avg = _slider(self.PIN, exempt=frozenset({"sector_equities"}))
        assert out["sector_equities"] == 500_000
        assert sum(out.values()) == POOL

    def test_the_same_amount_is_dropped_when_not_exempt(self):
        out, min_pct, _avg = _slider(self.PIN)
        assert out["sector_equities"] == 0
        assert min_pct == 8.0
        assert sum(out.values()) == POOL

    def test_the_exempt_total_is_out_of_the_redistribution_base(self):
        # us_equities (4%) is engine dust and still goes; the pin neither
        # absorbs any of the freed amount nor is rescaled by it.
        amounts = {
            "sector_equities": 500_000,  # pinned, 5%
            "us_equities": 400_000,  # engine dust, 4%
            "medium_beta_equities": 5_100_000,
            "high_beta_equities": 4_000_000,
        }
        out, _min_pct, _avg = _slider(amounts, exempt=frozenset({"sector_equities"}))
        assert out["sector_equities"] == 500_000
        assert out["us_equities"] == 0
        assert sum(out.values()) == POOL

    def test_omitting_exempt_is_the_old_behaviour(self):
        assert _slider(self.PIN) == _slider(self.PIN, exempt=frozenset())


def _phase4(**kw):
    from asset_allocation_pydantic.models import MultiAssetFundComposition
    from asset_allocation_pydantic.steps.step4_long_term import phase4_multi_asset

    comp = kw.pop("comp", (65.0, 25.0, 10.0))
    return phase4_multi_asset(
        equities_amount=kw.pop("eq", 6_500_000),
        debt_amount=kw.pop("dt", 2_500_000),
        others_amount=kw.pop("oth", 1_000_000),
        composition=MultiAssetFundComposition(
            equity_pct=comp[0], debt_pct=comp[1], others_pct=comp[2]
        ),
        **kw,
    )


class TestPhase4RequestedSleeve:
    """A caller may ask for a specific sleeve size instead of the auto-size.
    The ask replaces the ``min()`` policy, but the class split stays the outer
    truth (D-A4): no slice of the sleeve may over-draw the class that funds it.
    """

    # 6.5m / 2.5m / 1.0m at 65/25/10 auto-sizes to 5,000,000 (the 0.50
    # diversification cap on equity binds); every class room sits at 10,000,000.
    AUTO = 5_000_000

    def test_a_request_inside_every_class_budget_is_used_verbatim(self):
        assert _phase4(requested_amount=2_000_000).multi_asset_amount == 2_000_000
        assert _phase4().multi_asset_amount == self.AUTO  # and it is not the auto-size

    def test_a_request_above_the_auto_cap_is_honoured(self):
        # The 0.50 cap is the AUTO path's diversification policy; a caller that
        # names a size has already made that call for itself.
        assert _phase4(requested_amount=8_000_000).multi_asset_amount == 8_000_000

    def test_a_request_over_drawing_debt_is_clamped_to_the_debt_room(self):
        block = _phase4(dt=1_000_000, requested_amount=8_000_000)
        assert block.multi_asset_amount == 4_000_000  # 1,000,000 / 0.25
        assert block.debt_component == 1_000_000
        assert block.debt_for_subgroups == 0

    def test_a_request_over_drawing_commodity_is_clamped_to_the_others_room(self):
        block = _phase4(oth=200_000, requested_amount=8_000_000)
        assert block.multi_asset_amount == 2_000_000  # 200,000 / 0.10
        assert block.others_component == 200_000
        assert block.remaining_others_for_gold == 0

    def test_a_request_over_drawing_equity_is_clamped_to_the_equity_room(self):
        block = _phase4(eq=1_300_000, requested_amount=8_000_000)
        assert block.multi_asset_amount == 2_000_000  # 1,300,000 / 0.65
        assert block.equity_component == 1_300_000
        assert block.equity_for_subgroups == 0

    def test_a_request_keeps_the_zero_sleeve_guards(self):
        assert _phase4(dt=0, requested_amount=2_000_000).multi_asset_amount == 0
        assert _phase4(eq=0, requested_amount=2_000_000).multi_asset_amount == 0
        assert _phase4(requested_amount=0).multi_asset_amount == 0


class TestPhase4AutoSizeUnchanged:
    """``requested_amount=None`` must reproduce the pre-preference auto-size
    field for field — both live callers rely on it."""

    # (equities, debt, others, composition) -> the block today's code returns.
    PINNED = [
        (6500000, 2500000, 1000000, (65.0, 25.0, 10.0), {'multi_asset_amount': 5000000, 'equity_component': 3250000, 'debt_component': 1250000, 'others_component': 500000, 'equity_for_subgroups': 3250000, 'debt_for_subgroups': 1250000, 'remaining_others_for_gold': 500000}),  # default 65/25/10
        (6500000, 2500000, 0, (65.0, 25.0, 10.0), {'multi_asset_amount': 5000000, 'equity_component': 3250000, 'debt_component': 1250000, 'others_component': 500000, 'equity_for_subgroups': 2750000, 'debt_for_subgroups': 1250000, 'remaining_others_for_gold': 0}),  # others gate zeroed
        (6500000, 1000000, 1000000, (65.0, 25.0, 10.0), {'multi_asset_amount': 4000000, 'equity_component': 2600000, 'debt_component': 1000000, 'others_component': 400000, 'equity_for_subgroups': 3900000, 'debt_for_subgroups': 0, 'remaining_others_for_gold': 600000}),  # debt room binds
        (0, 2500000, 1000000, (65.0, 25.0, 10.0), {'multi_asset_amount': 0, 'equity_component': 0, 'debt_component': 0, 'others_component': 0, 'equity_for_subgroups': 0, 'debt_for_subgroups': 2500000, 'remaining_others_for_gold': 1000000}),  # zero equity
        (6500000, 0, 1000000, (65.0, 25.0, 10.0), {'multi_asset_amount': 0, 'equity_component': 0, 'debt_component': 0, 'others_component': 0, 'equity_for_subgroups': 6500000, 'debt_for_subgroups': 0, 'remaining_others_for_gold': 1000000}),  # zero debt
        (6500000, 2500000, 1000000, (65.0, 0.0, 35.0), {'multi_asset_amount': 5000000, 'equity_component': 3250000, 'debt_component': 0, 'others_component': 1750000, 'equity_for_subgroups': 2500000, 'debt_for_subgroups': 2500000, 'remaining_others_for_gold': 0}),  # no debt slice (INF)
        (6500000, 2500000, 1000000, (0.0, 50.0, 50.0), {'multi_asset_amount': 5000000, 'equity_component': 0, 'debt_component': 2500000, 'others_component': 2500000, 'equity_for_subgroups': 5000000, 'debt_for_subgroups': 0, 'remaining_others_for_gold': 0}),  # no equity slice (INF)
        (6500000, 2500000, 1000000, (0.0, 0.0, 100.0), {'multi_asset_amount': 0, 'equity_component': 0, 'debt_component': 0, 'others_component': 0, 'equity_for_subgroups': 6500000, 'debt_for_subgroups': 2500000, 'remaining_others_for_gold': 1000000}),  # both slices zero
        (1234567, 987654, 345678, (65.0, 25.0, 10.0), {'multi_asset_amount': 949700, 'equity_component': 617300, 'debt_component': 237400, 'others_component': 95000, 'equity_for_subgroups': 617300, 'debt_for_subgroups': 750300, 'remaining_others_for_gold': 250700}),  # rounding-sensitive
    ]

    def test_every_field_of_the_auto_sized_block_is_unmoved(self):
        for eq, dt, oth, comp, expected in self.PINNED:
            assert _phase4(eq=eq, dt=dt, oth=oth, comp=comp).model_dump() == expected

    def test_omitting_the_request_is_the_same_call(self):
        assert _phase4().model_dump() == _phase4(requested_amount=None).model_dump()

    def test_a_randomised_sweep_of_the_auto_path_is_byte_stable(self):
        """2,000 seeded (class amounts x composition) draws, digested. Any drift
        in the ``None`` path — including the INF / non-positive guards — moves
        this hash, which was taken from the code as it stood before Task A3."""
        import hashlib
        import random

        from asset_allocation_pydantic.models import MultiAssetFundComposition
        from asset_allocation_pydantic.steps.step4_long_term import phase4_multi_asset

        rng = random.Random(20260914)
        digest = hashlib.sha256()
        for _ in range(2000):
            eq = rng.randrange(0, 50_000_000)
            dt = rng.randrange(0, 20_000_000)
            oth = rng.randrange(0, 10_000_000)
            e = rng.randrange(0, 101)
            d = rng.randrange(0, 101 - e)
            o = 100 - e - d
            block = phase4_multi_asset(
                eq,
                dt,
                oth,
                MultiAssetFundComposition(
                    equity_pct=float(e), debt_pct=float(d), others_pct=float(o)
                ),
            )
            digest.update(
                repr(
                    (eq, dt, oth, e, d, o, tuple(sorted(block.model_dump().items())))
                ).encode()
            )
        assert (
            digest.hexdigest()
            == "b9f33371742e86fbd888eb3f16332c30a02d1e3f4ee9ebab9aa1281721151d0a"
        )


# ── Task B1 — pin extraction, reconciliation, and the uniform sleeve min() ────


def _prefs(**kw):
    from practical_asset_allocation.human_override import HumanOverridePreferences

    return HumanOverridePreferences(**kw)


# A portfolio whose pins are easy to read off by hand.
TOTAL_CORPUS = 10_000_000


class TestSubgroupPins:
    """Every settable sub-group ask becomes a rupee pin against the WHOLE
    portfolio (D-A3); a share of zero or less is an exclusion, not a pin."""

    def test_no_preference_is_no_pins(self):
        from practical_asset_allocation.pipeline import _subgroup_pins

        assert _subgroup_pins(None, TOTAL_CORPUS) == ({}, frozenset(), False)

    def test_a_class_only_preference_is_no_pins(self):
        from practical_asset_allocation.pipeline import _subgroup_pins

        prefs = _prefs(asset_class_requested={"equity": 85.0, "debt": 7.0, "others": 8.0})
        assert _subgroup_pins(prefs, TOTAL_CORPUS) == ({}, frozenset(), False)

    def test_a_share_converts_against_the_total_corpus(self):
        from practical_asset_allocation.pipeline import _subgroup_pins

        pins, excluded, gold_excluded = _subgroup_pins(
            _prefs(subgroup_emphasis={"low_beta_equities": 40.0}), TOTAL_CORPUS
        )
        assert pins == {"low_beta_equities": 4_000_000}
        assert excluded == frozenset() and gold_excluded is False

    def test_the_class_no_longer_changes_the_arithmetic(self):
        """D-A3 — the same share is the same rupees whichever class the row
        belongs to; the %-of-class routing is gone."""
        from practical_asset_allocation.pipeline import _subgroup_pins

        debt, _excluded, _gold = _subgroup_pins(
            _prefs(subgroup_emphasis={"arbitrage_plus_income": 25.0}), TOTAL_CORPUS
        )
        gold, _excluded, _gold_flag = _subgroup_pins(
            _prefs(subgroup_emphasis={"gold_commodities": 25.0}), TOTAL_CORPUS
        )
        assert debt == {"arbitrage_plus_income": 2_500_000}
        assert gold == {"gold_commodities": 2_500_000}

    def test_a_zero_share_is_an_exclusion_not_a_pin(self):
        from practical_asset_allocation.pipeline import _subgroup_pins

        pins, excluded, gold_excluded = _subgroup_pins(
            _prefs(subgroup_emphasis={"us_equities": 0.0, "low_beta_equities": 40.0}),
            TOTAL_CORPUS,
        )
        assert pins == {"low_beta_equities": 4_000_000}
        assert excluded == frozenset({"us_equities"})
        assert gold_excluded is False

    def test_excluding_gold_is_surfaced_to_the_caller(self):
        """D-B3 — gold is the only dedicated commodity vehicle, so the caller
        has to zero the commodity class (and with it the sleeve, D-B2)."""
        from practical_asset_allocation.pipeline import _subgroup_pins

        pins, excluded, gold_excluded = _subgroup_pins(
            _prefs(subgroup_emphasis={"gold_commodities": 0.0}), TOTAL_CORPUS
        )
        assert pins == {}
        assert excluded == frozenset({"gold_commodities"})
        assert gold_excluded is True

    def test_frozen_holdings_rows_are_ignored(self):
        """ELSS and direct stock are holdings, not preferences. The pydantic
        model already rejects them, so this is defence in depth against a
        hand-built prefs object."""
        from types import SimpleNamespace

        from practical_asset_allocation.pipeline import _subgroup_pins

        stub = SimpleNamespace(
            subgroup_emphasis={
                "tax_efficient_equities": 50.0,
                "non_mf_equities": 0.0,
                "low_beta_equities": 10.0,
            }
        )
        pins, excluded, gold_excluded = _subgroup_pins(stub, TOTAL_CORPUS)
        assert pins == {"low_beta_equities": 1_000_000}
        assert excluded == frozenset() and gold_excluded is False


class TestFitPinsToRoom:
    """D-A4 — over-subscribed pins scale down proportionally and the caller
    discloses; they are never silently dropped."""

    def test_pins_inside_the_room_are_untouched(self):
        from practical_asset_allocation.pipeline import _fit_pins_to_room

        pins = {"low_beta_equities": 4_000_000, "us_equities": 1_000_000}
        assert _fit_pins_to_room(pins, 10_000_000) == (pins, False)

    def test_no_pins_never_reports_a_scale_down(self):
        from practical_asset_allocation.pipeline import _fit_pins_to_room

        assert _fit_pins_to_room({}, 0) == ({}, False)

    def test_over_subscribed_pins_scale_proportionally(self):
        from practical_asset_allocation.pipeline import _fit_pins_to_room

        out, scaled = _fit_pins_to_room(
            {"low_beta_equities": 6_000_000, "us_equities": 2_000_000}, 4_000_000
        )
        assert scaled is True
        assert out == {"low_beta_equities": 3_000_000, "us_equities": 1_000_000}

    def test_the_scaled_pins_always_fit(self):
        """Phase 5 raises when the pins exceed the pool, so rounding drift must
        never push the scaled set back over the room."""
        from practical_asset_allocation.pipeline import _fit_pins_to_room

        out, scaled = _fit_pins_to_room(
            {"a": 3_333_333, "b": 6_666_667, "c": 1}, 5_000_000
        )
        assert scaled is True
        assert sum(out.values()) <= 5_000_000

    def test_a_zero_room_zeroes_every_pin_and_discloses(self):
        from practical_asset_allocation.pipeline import _fit_pins_to_room

        out, scaled = _fit_pins_to_room({"low_beta_equities": 4_000_000}, 0)
        assert out == {"low_beta_equities": 0} and scaled is True


def _sleeve(**kw):
    from practical_asset_allocation.pipeline import _sleeve_size

    comp = kw.pop("comp", (0.65, 0.25, 0.10))
    return _sleeve_size(
        deployable_equity=kw.pop("eq", 10_000_000),
        debt=kw.pop("dt", 10_000_000),
        others=kw.pop("oth", 10_000_000),
        pins=kw.pop("pins", {}),
        eq_pct=comp[0],
        dt_pct=comp[1],
        oth_pct=comp[2],
        requested=kw.pop("requested", None),
        preference_set=kw.pop("preference_set", False),
    )


class TestSleeveSize:
    """The uniform `min()`. Every sub-group pin — equity, debt or gold — is a
    bound on how large the multi-asset sleeve may be; the tightest binds."""

    # 0.50 x 10,000,000 / 0.65, rounded to 100.
    CAP = 7_692_300

    def test_the_diversification_cap_binds_when_nothing_else_does(self):
        assert _sleeve() == self.CAP

    def test_the_equity_room_binds_when_equity_is_pinned(self):
        # (10,000,000 - 8,000,000) / 0.65 = 3,076,923 -> 3,076,900.
        assert _sleeve(pins={"low_beta_equities": 8_000_000}) == 3_076_900

    def test_the_debt_room_binds(self):
        # (1,000,000 - 0) / 0.25 = 4,000,000, tighter than the 0.50 cap.
        assert _sleeve(dt=1_000_000) == 4_000_000

    def test_a_debt_pin_tightens_the_debt_room(self):
        # (2,000,000 - 1,000,000) / 0.25 = 4,000,000.
        assert _sleeve(dt=2_000_000, pins={"arbitrage_plus_income": 1_000_000}) == 4_000_000

    def test_the_commodity_room_binds_when_a_preference_is_set(self):
        # D-B1 — 500,000 / 0.10 = 5,000,000, tighter than the cap.
        assert _sleeve(oth=500_000, preference_set=True) == 5_000_000

    def test_a_gold_pin_tightens_the_commodity_room(self):
        # (1,000,000 - 500,000) / 0.10 = 5,000,000.
        assert (
            _sleeve(oth=1_000_000, pins={"gold_commodities": 500_000}, preference_set=True)
            == 5_000_000
        )

    def test_the_commodity_room_is_ignored_with_no_preference(self):
        """D-B1 — on the default path the others-gate can zero commodity, and
        bounding by others/0.10 = 0 would destroy a sleeve that is fine today."""
        assert _sleeve(oth=0, preference_set=False) == self.CAP

    def test_excluding_gold_zeroes_the_sleeve(self):
        """D-B3 -> D-B2 — the caller zeroes the commodity class, and a sleeve
        that is 10% commodity by construction cannot then exist."""
        assert _sleeve(oth=0, preference_set=True) == 0

    def test_excluding_gold_zeroes_the_sleeve_with_no_class_preference(self):
        """D-B4 — `preference_set` means ANY preference. A customer who only
        excludes gold must still get the commodity bound, or the sleeve would
        hand them back the gold they refused."""
        assert _sleeve(oth=0, pins={}, preference_set=True) == 0

    def test_a_request_is_used_when_every_room_allows_it(self):
        assert _sleeve(requested=3_000_000, preference_set=True) == 3_000_000

    def test_a_request_above_the_cap_is_honoured(self):
        """D-A6 — the 0.50 cap is the engine's own diversification policy; a
        customer who named a sleeve size has made that judgement themselves."""
        assert _sleeve(requested=9_000_000, preference_set=True) == 9_000_000

    def test_a_request_is_still_clamped_by_the_debt_room(self):
        assert _sleeve(dt=1_000_000, requested=20_000_000, preference_set=True) == 4_000_000

    def test_a_request_is_still_clamped_by_the_commodity_room(self):
        assert _sleeve(oth=500_000, requested=20_000_000, preference_set=True) == 5_000_000

    def test_a_request_is_still_clamped_by_the_equity_room(self):
        # The FULL equity room (10,000,000 / 0.65), not the 0.50 cap.
        assert _sleeve(requested=20_000_000, preference_set=True) == 15_384_600

    def test_the_zero_sleeve_guards_survive_a_request(self):
        assert _sleeve(eq=0, requested=2_000_000, preference_set=True) == 0
        assert _sleeve(dt=0, requested=2_000_000, preference_set=True) == 0
        assert _sleeve(requested=0, preference_set=True) == 0

    def test_a_composition_slice_of_zero_does_not_bound(self):
        # No equity slice: the equity room and the cap are both infinite, so
        # the debt room decides (matches phase4's INF handling).
        assert _sleeve(comp=(0.0, 0.5, 0.5), dt=1_000_000) == 2_000_000


class TestSleeveSizeNoDrift:
    """STEP 5 GUARD — with NO pins and `preference_set=False`, `_sleeve_size`
    must reproduce today's `phase4_multi_asset` auto-size EXACTLY. This is the
    proof that the default path never moves. If it fails, the engine drifted —
    fix the engine, never this test."""

    # Chosen so the three variants bind on different terms (asserted below).
    VARIANTS = {
        "default": {},
        "heavy_elss": {"elss_corpus": 5_000_000.0},
        "risk_9_5": {"effective_risk_score": 9.5},
    }

    @staticmethod
    def _live(**overrides):
        from practical_asset_allocation.pipeline import run_practical_allocation

        trace: dict = {}
        inp = make_practical_input(**overrides)
        run_practical_allocation(inp, trace=trace)
        return inp, trace["step4_long_term"]

    def test_the_live_engine_sleeve_is_reproduced_on_every_variant(self):
        from practical_asset_allocation.pipeline import _sleeve_size

        for name, overrides in self.VARIANTS.items():
            inp, s4 = self._live(**overrides)
            comp = inp.multi_asset_composition
            got = _sleeve_size(
                deployable_equity=s4["residual_equity_corpus_pre_multi_asset"],
                debt=s4["debt_amount"],
                others=s4["others_amount"],
                pins={},
                eq_pct=comp.equity_pct / 100.0,
                dt_pct=comp.debt_pct / 100.0,
                oth_pct=comp.others_pct / 100.0,
                requested=None,
                preference_set=False,
            )
            live = s4["multi_asset_block"]["multi_asset_amount"]
            assert got == live, f"{name}: sleeve drifted, {got} != {live}"

    def test_phase4_and_the_helper_agree_field_for_field(self):
        from asset_allocation_pydantic.steps.step4_long_term import phase4_multi_asset
        from practical_asset_allocation.pipeline import _sleeve_size

        for name, overrides in self.VARIANTS.items():
            inp, s4 = self._live(**overrides)
            comp = inp.multi_asset_composition
            block = phase4_multi_asset(
                equities_amount=s4["residual_equity_corpus_pre_multi_asset"],
                debt_amount=s4["debt_amount"],
                others_amount=s4["others_amount"],
                composition=comp,
            )
            got = _sleeve_size(
                deployable_equity=s4["residual_equity_corpus_pre_multi_asset"],
                debt=s4["debt_amount"],
                others=s4["others_amount"],
                pins={},
                eq_pct=comp.equity_pct / 100.0,
                dt_pct=comp.debt_pct / 100.0,
                oth_pct=comp.others_pct / 100.0,
                requested=None,
                preference_set=False,
            )
            assert got == block.multi_asset_amount, f"{name}: {got} != auto-size"

    def test_the_variants_really_do_bind_on_different_terms(self):
        """Without this the guard above could pass vacuously — all three
        variants binding on the same term would test one branch three times."""
        expected = {
            "default": (5_432_300, 7_062_000, 8_668_000, 1_970_000),  # 0.50 cap binds
            "heavy_elss": (2_355_400, 3_062_000, 8_668_000, 1_970_000),  # 0.50 cap binds
            "risk_9_5": (11_032_000, 14_942_000, 2_758_000, 0),  # debt room binds
        }
        for name, overrides in self.VARIANTS.items():
            _inp, s4 = self._live(**overrides)
            got = (
                s4["multi_asset_block"]["multi_asset_amount"],
                s4["residual_equity_corpus_pre_multi_asset"],
                s4["debt_amount"],
                s4["others_amount"],
            )
            assert got == expected[name], f"{name}: {got} != {expected[name]}"
        # risk_9_5 has others == 0: proof the commodity term MUST stay off the
        # default path (D-B1), or this variant's sleeve would collapse to 0.


# ── Task B2 — the carve wired into the long-term step ────────────────────────
#
# READ THIS BEFORE ADDING AN ASSERTION HERE. Task B2 wires the pins into the
# ENGINE (phases 4 and 5 plus the sleeve bound). Step 6's reshape is still live
# until Task C1 retires it, and it re-applies the very same emphasis against a
# DIFFERENT basis — the migratable rows of the class, which exclude locked ELSS
# / direct stock and the multi-asset sleeve — so it overwrites the engine's
# placement on the way out. Pin HONOURING is therefore asserted on the
# long-term step's own numbers (the trace), which is exactly what this task
# owns and what survives once step 6 stops reshaping.
#
# The CLASS mix, the sleeve and the class residuals ARE read off the finished
# output: step 6's reshape is within-class and leaves all three untouched.

# The live neutral sub-group mix, captured before Part A (plan Task A0 Step 2).
# Percentages of grand_total, 2dp, rows with a positive total.
NEUTRAL_SUBGROUP_MIX = {
    "short_debt": 1.5,
    "arbitrage_plus_income": 36.55,
    "multi_asset": 27.16,
    "low_beta_equities": 10.28,
    "us_equities": 7.38,
    "gold_commodities": 7.13,
    "tax_efficient_equities": 5.0,
    "non_mf_equities": 5.0,
}

C_85_7_8 = {"equity": 85.0, "debt": 7.0, "others": 8.0}


def _run(class_pcts=None, emphasis=None, **overrides):
    """Run the practical engine end to end.

    Returns ``(input, output, step4_trace)`` — the trace is the long-term
    step's internal state, i.e. what Task B2 actually decides.
    """
    from practical_asset_allocation.human_override import HumanOverridePreferences
    from practical_asset_allocation.pipeline import run_practical_allocation

    inp = make_practical_input(**overrides)
    if class_pcts is not None or emphasis is not None:
        inp = inp.model_copy(
            update={
                "human_override": HumanOverridePreferences(
                    asset_class_requested=class_pcts,
                    subgroup_emphasis=emphasis or {},
                )
            }
        )
    trace: dict = {}
    out = run_practical_allocation(inp, trace=trace)
    return inp, out, trace["step4_long_term"]


def _mix(out):
    r = out.asset_class_breakdown.recommended
    return r.equity_total_pct, r.debt_total_pct, r.others_total_pct


def _pin_of(inp, share: float) -> int:
    """The rupee pin a ``share``-of-TOTAL ask becomes — the same one multiply
    ``_subgroup_pins`` performs (D-A3)."""
    from asset_allocation_pydantic.utils import round_to_100

    return round_to_100(inp.total_corpus * share / 100.0)


class TestEquityPinsWiredIn:
    def test_an_equity_pin_is_placed_and_the_class_mix_still_lands(self):
        inp, out, s4 = _run(C_85_7_8, {"low_beta_equities": 34.0})
        assert s4["equity_subgroup_amounts"]["low_beta_equities"] == _pin_of(inp, 34.0)
        eq, dt, ot = _mix(out)
        assert abs(eq - 85.0) < 1.5
        assert abs(dt - 7.0) < 1.5
        assert abs(ot - 8.0) < 1.5

    def test_a_pinned_row_survives_the_slider(self):
        """D-A1 — a 2.5%-of-total ask is well under the slider's drop bar, and
        the slider cannot tell a customer's number from engine dust."""
        inp, _out, s4 = _run(C_85_7_8, {"value_equities": 2.5})
        assert s4["equity_subgroup_amounts"]["value_equities"] == _pin_of(inp, 2.5)

    def test_over_subscribed_pins_scale_down_and_are_flagged(self):
        """D-A4 — 95% of the PORTFOLIO cannot fit the equity that can
        actually be bought (ELSS and direct stock outrank the ask), so the pins
        scale proportionally and the run carries the disclosure flag."""
        _inp, _out, s4 = _run(
            C_85_7_8, {"low_beta_equities": 55.0, "us_equities": 40.0}
        )
        assert s4["pins_scaled"] is True
        lb = s4["equity_subgroup_amounts"]["low_beta_equities"]
        us = s4["equity_subgroup_amounts"]["us_equities"]
        assert lb + us <= s4["residual_equity_corpus_final"]
        assert abs(lb / us - 55.0 / 40.0) < 0.01

    def test_pins_that_fit_are_not_flagged(self):
        _inp, _out, s4 = _run(C_85_7_8, {"low_beta_equities": 34.0})
        assert s4["pins_scaled"] is False


class TestGoldAndDebtPinsBoundTheSleeve:
    def test_a_gold_pin_is_a_floor_and_the_commodity_class_still_lands(self):
        """D-A5 — gold is the commodity residual, so the pin enters only as a
        sleeve bound and the residual comes out AT or ABOVE the ask. 6.7% of
        total makes the commodity room the binding term, so it lands exactly."""
        inp, out, s4 = _run(C_85_7_8, {"gold_commodities": 6.7})
        pin = _pin_of(inp, 6.7)
        assert s4["long_term_subgroup_amounts"]["gold_commodities"] >= pin
        assert s4["residual_other_corpus"] == pin
        assert abs(_mix(out)[2] - 8.0) < 1.5

    def test_a_debt_pin_is_a_floor_and_the_debt_class_still_lands(self):
        inp, out, s4 = _run(C_85_7_8, {"arbitrage_plus_income": 3.0})
        pin = _pin_of(inp, 3.0)
        assert s4["residual_debt_corpus"] >= pin
        assert abs(_mix(out)[1] - 7.0) < 1.5

    def test_a_multi_asset_pin_sizes_the_sleeve(self):
        """D-A2/D-A6 — the sleeve is a sub-group like any other; a pin inside
        every class room is used verbatim."""
        inp, out, s4 = _run(C_85_7_8, {"multi_asset": 17.0})
        assert s4["multi_asset_block"]["multi_asset_amount"] == _pin_of(inp, 17.0)
        eq, dt, ot = _mix(out)
        assert abs(eq - 85.0) < 1.5
        assert abs(dt - 7.0) < 1.5
        assert abs(ot - 8.0) < 1.5


class TestEquityStillAddsUp:
    """Spec §5 — the dedicated equity rows plus the sleeve's equity slice must
    account for the whole equity class. This is THE guard that no rupee is
    stranded when the pins move the sleeve around; if it cannot pass, stop."""

    CASES = [
        ("class only", C_85_7_8, None),
        ("equity pin", C_85_7_8, {"low_beta_equities": 40.0}),
        ("gold pin", C_85_7_8, {"gold_commodities": 85.0}),
        ("debt pin", C_85_7_8, {"arbitrage_plus_income": 50.0}),
        ("multi-asset pin", C_85_7_8, {"multi_asset": 20.0}),
        ("multi-asset excluded", C_85_7_8, {"multi_asset": 0.0}),
        ("over-subscribed", C_85_7_8, {"low_beta_equities": 55.0, "us_equities": 40.0}),
        ("subgroup only", None, {"low_beta_equities": 30.0}),
        ("no preference", None, None),
    ]

    def test_dedicated_equity_plus_the_sleeve_slice_is_the_equity_class(self):
        for name, class_pcts, emphasis in self.CASES:
            inp, _out, s4 = _run(class_pcts, emphasis)
            eq_frac = inp.multi_asset_composition.equity_pct / 100.0
            dedicated = (
                sum(s4["equity_subgroup_amounts"].values())
                + s4["elss_amount_frozen"]
                + s4["non_mf_equity_actual"]
            )
            sleeve_equity = eq_frac * s4["multi_asset_block"]["multi_asset_amount"]
            assert abs(dedicated + sleeve_equity - s4["equities_amount"]) <= 5_000, (
                f"{name}: equity does not add up"
            )


class TestExcludingGold:
    """D-B3 → D-B2 — gold is the only dedicated commodity vehicle, so 'no gold'
    empties the commodity class, and a sleeve that is 10% commodity by
    construction cannot exist without it."""

    def test_the_commodity_class_and_the_sleeve_both_go_to_zero(self):
        _inp, out, s4 = _run(C_85_7_8, {"gold_commodities": 0.0})
        assert s4["others_amount"] == 0
        assert s4["long_term_subgroup_amounts"]["gold_commodities"] == 0
        assert s4["multi_asset_block"]["multi_asset_amount"] == 0
        rows = {r.subgroup: r.total for r in out.aggregated_subgroups}
        assert rows.get("gold_commodities", 0.0) == 0.0
        assert rows.get("multi_asset", 0.0) == 0.0
        # The debt class still lands — now carried by dedicated debt funds
        # alone, since the sleeve was delivering a quarter of it.
        eq, dt, ot = _mix(out)
        assert abs(dt - 7.0) < 1.5
        assert ot == 0.0
        assert out.human_override_applied.shortfall_reason is not None

    def test_the_same_holds_with_no_class_preference(self):
        """D-B4 — ``preference_set`` is ANY preference. A customer who only
        says 'no gold' must still get the commodity bound, or the sleeve would
        hand them back the gold they refused."""
        _inp, out, s4 = _run(None, {"gold_commodities": 0.0})
        assert s4["others_amount"] == 0
        assert s4["long_term_subgroup_amounts"]["gold_commodities"] == 0
        assert s4["multi_asset_block"]["multi_asset_amount"] == 0
        rows = {r.subgroup: r.total for r in out.aggregated_subgroups}
        assert rows.get("gold_commodities", 0.0) == 0.0
        assert rows.get("multi_asset", 0.0) == 0.0


class TestExcludingMultiAsset:
    """D-A2 — the sleeve is a sub-group like any other, so refusing it is a
    refusal and not an absent ask. Nothing else empties the row: with no
    ``multi_asset`` key among the pins the sleeve would simply auto-size back
    to the fund the customer declined."""

    def test_the_sleeve_goes_to_zero_and_nothing_is_stranded(self):
        _inp, out, s4 = _run(C_85_7_8, {"multi_asset": 0.0})
        assert s4["multi_asset_block"]["multi_asset_amount"] == 0
        rows = {r.subgroup: r.total for r in out.aggregated_subgroups}
        assert rows.get("multi_asset", 0.0) == 0.0
        # Each class still lands: the slices the sleeve was carrying fall back
        # to the dedicated rows of the very classes that funded them.
        eq, dt, ot = _mix(out)
        assert abs(eq - 85.0) < 1.5
        assert abs(dt - 7.0) < 1.5
        assert abs(ot - 8.0) < 1.5
        assert abs(sum(rows.values()) - out.grand_total) < 1.0

    def test_the_same_holds_with_no_class_preference(self):
        _inp, out, s4 = _run(None, {"multi_asset": 0.0})
        assert s4["multi_asset_block"]["multi_asset_amount"] == 0
        rows = {r.subgroup: r.total for r in out.aggregated_subgroups}
        assert rows.get("multi_asset", 0.0) == 0.0
        assert abs(sum(rows.values()) - out.grand_total) < 1.0


class TestTheDefaultPathNeverMoves:
    """With no preference the engine must behave exactly as it did before this
    plan. A failure here is a leak of the preference path into the default one."""

    def test_the_neutral_sub_group_mix_is_unchanged(self):
        _inp, out, _s4 = _run()
        g = out.grand_total
        got = {
            r.subgroup: round(100.0 * r.total / g, 2)
            for r in out.aggregated_subgroups
            if r.total > 0
        }
        assert got == NEUTRAL_SUBGROUP_MIX

    def test_phase_4_is_asked_for_no_sleeve_size_without_a_preference(self):
        """THE TRAP. phase 4's REQUESTED path clamps by the commodity room; its
        AUTO path does not. The default others-gate can legitimately zero
        commodity, so passing a request with no preference would collapse those
        profiles' sleeves to nothing. Prove the request is simply absent."""
        import practical_asset_allocation.pipeline as pipe

        seen: list = []
        real = pipe.phase4_multi_asset

        def spy(*args, **kwargs):
            seen.append(kwargs.get("requested_amount", "ABSENT"))
            return real(*args, **kwargs)

        pipe.phase4_multi_asset = spy
        try:
            _run()
            assert seen == ["ABSENT"]
            seen.clear()
            _run(C_85_7_8)
            assert seen and seen[0] != "ABSENT"
        finally:
            pipe.phase4_multi_asset = real

    def test_the_risk_9_5_sleeve_survives_its_zero_commodity_class(self):
        """The profile that proves the trap is real: others == 0 with a live
        ₹1.1cr sleeve. Clamping by the commodity room here would zero it."""
        _inp, _out, s4 = _run(effective_risk_score=9.5)
        assert s4["others_amount"] == 0
        assert s4["multi_asset_block"]["multi_asset_amount"] == 11_032_000


class TestExcludedResidualRowsAreActuallyEmptied:
    """D-B5 (spec 2026-09-14). Retiring step 6 removed the only thing that
    emptied an excluded RESIDUAL row. The debt class has two homes, so an
    exclusion re-routes to the other bucket; gold has one, so D-B3 zeroes the
    class instead. Without this, a customer who excluded a fund could be given
    36% of their portfolio in it."""

    def _run(self, **prefs):
        from practical_asset_allocation.human_override import HumanOverridePreferences
        from practical_asset_allocation.pipeline import run_practical_allocation

        out = run_practical_allocation(
            make_practical_input().model_copy(
                update={"human_override": HumanOverridePreferences(**prefs)}
            )
        )
        rows = {r.subgroup: r.total for r in out.aggregated_subgroups}
        return out, rows

    def test_excluded_debt_bucket_is_empty_without_a_class_preference(self):
        out, rows = self._run(subgroup_emphasis={"arbitrage_plus_income": 0.0})
        assert rows.get("arbitrage_plus_income", 0.0) == 0.0
        assert rows.get("short_debt", 0.0) > 0.0, "the money must go to the other debt bucket"
        assert abs(sum(rows.values()) - out.grand_total) < 1.0

    def test_excluded_debt_bucket_is_empty_with_a_class_preference(self):
        out, rows = self._run(
            asset_class_requested={"equity": 85.0, "debt": 7.0, "others": 8.0},
            subgroup_emphasis={"arbitrage_plus_income": 0.0},
        )
        assert rows.get("arbitrage_plus_income", 0.0) == 0.0
        assert abs(out.asset_class_breakdown.recommended.debt_total_pct - 7.0) < 1.5
        assert abs(sum(rows.values()) - out.grand_total) < 1.0

    def test_the_debt_class_is_preserved_by_the_reroute(self):
        """Re-routing must move the money, not destroy it.

        Both arms carry a preference, so both take the carve-outs-suspended
        path (spec 2026-09-15 §3) and the ONLY difference between them is the
        exclusion. A preference-free control would differ by the suspended
        emergency buffer as well, and measure that instead of the reroute."""
        mix = {"equity": 60.0, "debt": 30.0, "others": 10.0}
        control, _ = self._run(asset_class_requested=mix)
        excl, _ = self._run(
            asset_class_requested=mix,
            subgroup_emphasis={"arbitrage_plus_income": 0.0},
        )
        assert abs(
            control.asset_class_breakdown.recommended.debt_total_pct
            - excl.asset_class_breakdown.recommended.debt_total_pct
        ) < 0.5


class TestATrimmedPinIsNeverSilent:
    """D-A4: a pin the engine had to trim must be disclosed. The multi-asset
    ask needs its own notice — it is ONE fund, so it is capped by the class
    split rather than scaled proportionally like a share of a class pool."""

    def _run(self, **emphasis):
        from practical_asset_allocation.human_override import HumanOverridePreferences
        from practical_asset_allocation.pipeline import run_practical_allocation

        prefs = HumanOverridePreferences(
            asset_class_requested={"equity": 85.0, "debt": 7.0, "others": 8.0},
            subgroup_emphasis=emphasis or {},
        )
        return run_practical_allocation(
            make_practical_input().model_copy(update={"human_override": prefs})
        )

    def test_a_clamped_multi_asset_pin_is_disclosed(self):
        out = self._run(multi_asset=80.0)
        reason = out.human_override_applied.shortfall_reason
        assert reason is not None and "multi-asset" in reason

    def test_a_multi_asset_pin_that_fits_is_not_disclosed(self):
        assert trim_disclosure(self._run(multi_asset=20.0).human_override_applied) is None

    def test_excluding_multi_asset_is_not_a_trim(self):
        """Zero is honoured exactly, so there is nothing to disclose."""
        assert trim_disclosure(self._run(multi_asset=0.0).human_override_applied) is None

    def test_over_subscribed_equity_pins_keep_their_own_wording(self):
        out = self._run(low_beta_equities=60.0, medium_beta_equities=60.0)
        reason = out.human_override_applied.shortfall_reason
        assert reason is not None and "scaled proportionally" in reason
        assert "multi-asset" not in reason


class TestTheDisclosureNamesTheRealCause:
    """A disclosure that blames the wrong thing is worse than none. Refusing
    gold zeroes the commodity class (D-B3) and the sleeve with it (D-B2), which
    moves the mix off the class target — that must not be reported as "locked
    holdings" for a customer who has none."""

    CLASS = {"equity": 85.0, "debt": 7.0, "others": 8.0}

    def _run(self, inp, **emphasis):
        from practical_asset_allocation.human_override import HumanOverridePreferences
        from practical_asset_allocation.pipeline import run_practical_allocation

        prefs = HumanOverridePreferences(
            asset_class_requested=self.CLASS, subgroup_emphasis=emphasis or {}
        )
        return run_practical_allocation(inp.model_copy(update={"human_override": prefs}))

    def test_excluding_gold_names_the_exclusion_not_locked_holdings(self):
        clean = make_practical_input(
            elss_corpus=0.0, non_mf_equity_corpus=0.0, mf_corpus=20_000_000.0
        )
        assert clean.elss_corpus == 0.0 and clean.non_mf_equity_corpus == 0.0
        reason = self._run(clean, gold_commodities=0.0).human_override_applied.shortfall_reason
        assert reason is not None
        assert "excluding gold" in reason
        assert "locked ELSS" not in reason, "must not blame holdings the customer does not have"

    def test_locked_holdings_still_get_the_committed_lead(self):
        locked = make_practical_input(elss_corpus=12_000_000.0, mf_corpus=7_000_000.0)
        from practical_asset_allocation.human_override import HumanOverridePreferences
        from practical_asset_allocation.pipeline import run_practical_allocation

        out = run_practical_allocation(
            locked.model_copy(update={"human_override": HumanOverridePreferences(
                asset_class_requested={"equity": 40.0, "debt": 50.0, "others": 10.0}
            )})
        )
        reason = out.human_override_applied.shortfall_reason
        assert reason is not None and "already committed" in reason
        assert "excluding gold" not in reason

    def test_the_gap_figures_are_still_reported(self):
        clean = make_practical_input(
            elss_corpus=0.0, non_mf_equity_corpus=0.0, mf_corpus=20_000_000.0
        )
        reason = self._run(clean, gold_commodities=0.0).human_override_applied.shortfall_reason
        assert "asked 8%" in reason and "landed 0.0%" in reason


class TestAPinNeverAbsorbsTheSliderFreedRoom:
    """D-A1 — a pin large enough to leave only slivers behind must still come
    out exactly as typed.

    The slider's drop bar is measured against the TOTAL equity pool (the
    sleeve included), so once a big pin has taken most of the equity room
    EVERY remaining engine row can fall under it at once. The slider then
    correctly returns them all as zero — there is no survivor to redistribute
    to. That freed money is real corpus, not rounding noise: parking it on
    the pinned row hands the customer 55% of their portfolio in a category
    they asked 45% for, with no disclosure. It belongs back in the engine's
    own pre-slider split.
    """

    # 45% of the WHOLE portfolio in large-cap on a high-risk 85/10/5 ask: the
    # pin takes ~82% of the deployable equity residual, leaving three engine
    # rows at 2-5% of the total equity pool — all under the 8% bar.
    CORPUS = 28_930_900.0
    CLASS = {"equity": 85.0, "debt": 10.0, "others": 5.0}

    def _run(self):
        return _run(
            self.CLASS,
            {"low_beta_equities": 45.0},
            total_corpus=self.CORPUS,
            net_financial_assets=self.CORPUS,
            mf_corpus=self.CORPUS - 2_000_000.0,
            effective_risk_score=9.4,
        )

    def test_the_slider_really_does_zero_every_non_pinned_row(self):
        """The trigger this class exists for. If this stops holding, the rest
        of the class is no longer testing what it claims to."""
        from asset_allocation_pydantic.equity_subgroup_slider import (
            apply_equity_subgroup_slider,
        )

        _inp, _out, s4 = self._run()
        pre = s4["initial_equity_subgroup_amounts"]
        assert pre["low_beta_equities"] > 0
        assert sum(a for sg, a in pre.items() if sg != "low_beta_equities") > 0
        policed, _min_pct, _avg = apply_equity_subgroup_slider(
            pre,
            equity_pool=s4["residual_equity_corpus_final"],
            equities_amount=s4["equities_amount"],
            locked_amount=s4["elss_amount_frozen"] + s4["non_mf_equity_actual"],
            share_denominator=s4["equity_share_denominator"],
            exempt=frozenset({"low_beta_equities"}),
        )
        assert all(
            amt == 0 for sg, amt in policed.items() if sg != "low_beta_equities"
        ), "the slider is expected to drop every non-pinned row in this scenario"

    def test_the_pin_comes_out_exactly_as_asked(self):
        inp, _out, s4 = self._run()
        assert s4["equity_subgroup_amounts"]["low_beta_equities"] == _pin_of(inp, 45.0)

    def test_the_pinned_share_of_the_portfolio_is_the_share_asked_for(self):
        _inp, _out, s4 = self._run()
        placed = s4["equity_subgroup_amounts"]["low_beta_equities"]
        assert abs(placed * 100.0 / self.CORPUS - 45.0) < 0.5

    def test_the_freed_room_goes_back_to_the_engines_own_split(self):
        """Phase 5 always spends the whole pool, so the pre-slider split is
        exactly the freed room — restoring it conserves every rupee."""
        _inp, _out, s4 = self._run()
        assert s4["equity_subgroup_amounts"] == s4["initial_equity_subgroup_amounts"]

    def test_no_rupee_is_created_or_destroyed(self):
        _inp, _out, s4 = self._run()
        assert (
            sum(s4["equity_subgroup_amounts"].values())
            == s4["residual_equity_corpus_final"]
        )


class TestADebtPinRoutesTheResidualToItsOwnRow:
    """D-A5 promises the debt residual lands AT or ABOVE the ask — which can
    only be true if it lands in the row the customer named. The long-term
    debt residual used to be hard-wired to ``arbitrage_plus_income`` and only
    ever re-routed for an EXCLUSION (D-B5), so a customer who pinned
    ``short_debt`` got zero rupees in it and no disclosure."""

    CORPUS = 28_930_900.0
    CLASS = {"equity": 25.0, "debt": 65.0, "others": 10.0}

    def _run(self, emphasis=None):
        return _run(
            self.CLASS,
            emphasis,
            total_corpus=self.CORPUS,
            net_financial_assets=self.CORPUS,
            mf_corpus=self.CORPUS - 2_000_000.0,
        )

    def test_a_short_debt_pin_is_where_the_residual_lands(self):
        _inp, _out, s4 = self._run({"short_debt": 40.0})
        lt = s4["long_term_subgroup_amounts"]
        assert lt["short_debt"] == s4["residual_debt_corpus"]
        assert lt["arbitrage_plus_income"] == 0

    def test_the_short_debt_residual_is_at_or_above_the_ask(self):
        inp, _out, s4 = self._run({"short_debt": 40.0})
        assert s4["long_term_subgroup_amounts"]["short_debt"] >= _pin_of(inp, 40.0)

    def test_pinning_the_default_row_still_lands_there(self):
        _inp, _out, s4 = self._run({"arbitrage_plus_income": 40.0})
        lt = s4["long_term_subgroup_amounts"]
        assert lt["arbitrage_plus_income"] == s4["residual_debt_corpus"]
        assert lt["short_debt"] == 0

    def test_no_debt_pin_still_defaults_to_arbitrage_plus_income(self):
        _inp, _out, s4 = self._run({"low_beta_equities": 10.0})
        lt = s4["long_term_subgroup_amounts"]
        assert lt["arbitrage_plus_income"] == s4["residual_debt_corpus"]
        assert lt["short_debt"] == 0

    def test_the_debt_class_total_is_unmoved_by_where_it_lands(self):
        _i1, pinned, _s1 = self._run({"short_debt": 40.0})
        _i2, default, _s2 = self._run()
        assert (
            abs(
                pinned.asset_class_breakdown.recommended.debt_total_pct
                - default.asset_class_breakdown.recommended.debt_total_pct
            )
            < 0.5
        )

    def test_a_pin_that_lands_needs_no_disclosure(self):
        """A re-route that PLACES the pin is not a shortfall — only money the
        engine cannot place anywhere is."""
        _inp, out, _s4 = self._run({"short_debt": 40.0})
        assert trim_disclosure(out.human_override_applied) is None

    def test_excluding_the_pinned_row_is_impossible_but_an_exclusion_still_wins(self):
        """A share of 0 is an exclusion, never a pin, so the two can only meet
        across the two debt rows: pin one, refuse the other. D-B5 must still
        empty the refused row."""
        _inp, _out, s4 = self._run(
            {"short_debt": 40.0, "arbitrage_plus_income": 0.0}
        )
        lt = s4["long_term_subgroup_amounts"]
        assert lt["arbitrage_plus_income"] == 0
        assert lt["short_debt"] == s4["residual_debt_corpus"] > 0
