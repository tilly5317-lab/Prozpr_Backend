"""Spec 2026-09-15 §3/§4/§7 — a stated preference suspends the bucket carve-outs.

§3: if the customer has told us where their money goes, the engine is not also
deciding to hold back an emergency reserve, a near-term goal pot, or a liability
offset. Steps 1-3 are replaced with zeroed outputs carrying the whole corpus
forward, so the long-term step receives `rebalancing_corpus` intact.

§4: `arbitrage` becomes a subgroup step 4 may write to. Only reachable through a
preference — with none set the default debt row is unchanged, so Prozpr never
routes long-term money to plain arbitrage on its own.

§7: the long-term debt residual splits PRO-RATA across the debt rows the customer
named, rather than landing entirely in one. Amends D-A5: a debt row now lands AT
the ask (bar rounding), not at-or-above it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from test_human_override_golden import make_practical_input  # noqa: E402

CORPUS = 20_000_000.0


def _goal(months, amount=2_000_000.0, name="Car"):
    from asset_allocation_pydantic.models import Goal

    return Goal(
        goal_name=name,
        time_to_goal_months=months,
        amount_needed=amount,
        goal_priority="negotiable",
    )


def _run(**overrides):
    from practical_asset_allocation.pipeline import run_practical_allocation

    return run_practical_allocation(make_practical_input(**overrides))


def _prefs(**kw):
    from practical_asset_allocation.human_override import HumanOverridePreferences

    return HumanOverridePreferences(**kw)


def _buckets(out):
    return {b.bucket: b.allocated_amount for b in out.bucket_allocations}


def _subgroups(out):
    return {r.subgroup: r.total for r in out.aggregated_subgroups}


# The profile §3.3 measured against: carve-outs actually exercised.
_CARVEOUT_PROFILE = dict(
    emergency_fund_needed=True,
    elss_corpus=0.0,
    non_mf_equity_corpus=0.0,
    mf_corpus=CORPUS,
    goals=[_goal(18, 2_000_000.0), _goal(120, 10_000_000.0, "Retirement")],
)


class TestCarveOutSuspension:
    def test_without_a_preference_the_carve_outs_still_happen(self):
        """Baseline. If this ever goes quiet the suspension tests prove nothing."""
        out = _run(**_CARVEOUT_PROFILE)
        b = _buckets(out)

        assert b["emergency"] > 0, "emergency fund is carved when needed"
        assert b["short_term"] > 0, "an 18-month goal is funded short-term"

    def test_a_subgroup_preference_suspends_both_carve_outs(self):
        out = _run(
            **_CARVEOUT_PROFILE,
            human_override=_prefs(subgroup_emphasis={"low_beta_equities": 20.0}),
        )
        b = _buckets(out)

        assert b["emergency"] == 0
        assert b["short_term"] == 0
        assert b["medium_term"] == 0
        assert out.human_override_applied is not None

    def test_the_long_term_bucket_takes_the_whole_corpus(self):
        out = _run(
            **_CARVEOUT_PROFILE,
            human_override=_prefs(subgroup_emphasis={"low_beta_equities": 20.0}),
        )

        assert _buckets(out)["long_term"] == pytest.approx(CORPUS, abs=200)

    def test_a_bare_class_only_preference_suspends_them_too(self):
        """The blast radius, pinned deliberately: the gate is `preference_set`,
        which a class tilt alone satisfies (§3.5)."""
        out = _run(
            **_CARVEOUT_PROFILE,
            human_override=_prefs(
                asset_class_requested={"equity": 60.0, "debt": 35.0, "others": 5.0}
            ),
        )
        b = _buckets(out)

        assert b["emergency"] == 0 and b["short_term"] == 0
        assert b["long_term"] == pytest.approx(CORPUS, abs=200)
        assert out.human_override_applied is not None

    def test_an_exclusion_only_preference_suspends_them_too(self):
        """The equivalence the SIP branch rests on: `human_override_applied is
        not None` must hold even when the only preference set is a single
        subgroup exclusion, not a full pin or class tilt."""
        out = _run(
            **_CARVEOUT_PROFILE,
            human_override=_prefs(subgroup_emphasis={"gold_commodities": 0.0}),
        )
        b = _buckets(out)

        assert b["emergency"] == 0
        assert b["short_term"] == 0
        assert b["medium_term"] == 0
        assert out.human_override_applied is not None

    def test_the_four_bucket_output_shape_survives(self):
        """§3.3: `_build_output` still emits four rows, three of them at zero."""
        out = _run(
            **_CARVEOUT_PROFILE,
            human_override=_prefs(subgroup_emphasis={"low_beta_equities": 20.0}),
        )

        assert [b.bucket for b in out.bucket_allocations] == [
            "emergency",
            "short_term",
            "medium_term",
            "long_term",
        ]

    def test_conservation_holds_under_suspension(self):
        out = _run(
            **_CARVEOUT_PROFILE,
            human_override=_prefs(subgroup_emphasis={"low_beta_equities": 20.0}),
        )

        assert sum(_subgroups(out).values()) == pytest.approx(CORPUS, abs=500)


class TestNfaCarveOut:
    """§3.4: the NFA offset is a LIABILITY offset, computed regardless of
    `emergency_fund_needed`. It goes too — a distinct fact from the emergency
    fund, and the case that breaks the "already an exercised shape" argument."""

    _LEVERAGED = dict(
        emergency_fund_needed=False,
        net_financial_assets=-800_000.0,
        elss_corpus=0.0,
        non_mf_equity_corpus=0.0,
        mf_corpus=CORPUS,
    )

    def test_the_offset_is_carved_today(self):
        out = _run(**self._LEVERAGED)

        assert _buckets(out)["emergency"] == 800_000

    def test_a_preference_suspends_the_offset(self):
        out = _run(
            **self._LEVERAGED,
            human_override=_prefs(subgroup_emphasis={"low_beta_equities": 20.0}),
        )

        assert _buckets(out)["emergency"] == 0
        assert _buckets(out)["long_term"] == pytest.approx(CORPUS, abs=200)


MULTI_ASSET_SPLIT = {"equity": 0.65, "debt": 0.25, "others": 0.10}


def _class_mix_for(emphasis: dict) -> dict:
    """The class bar a COMPLETE distribution implies, attributing multi_asset
    65/25/10 the way the engine carves it (§5). This is what the screen sends
    alongside the pins, so a test that omits it is not testing the screen's
    payload — the engine's own risk-score bar would fight the asks and every
    pin would scale."""
    from practical_asset_allocation.human_override import CLASS_OF

    mix = {"equity": 0.0, "debt": 0.0, "others": 0.0}
    for sg, pct in emphasis.items():
        if sg == "multi_asset":
            for cls, share in MULTI_ASSET_SPLIT.items():
                mix[cls] += pct * share
        else:
            mix[CLASS_OF[sg]] += pct
    return mix


def _distribution(class_mix=None, **emphasis):
    """Run a complete distribution — pins plus the class bar they imply.

    `class_mix` is only passed where the pins deliberately do NOT cover the
    whole portfolio; the screen always sends a mix summing to 100."""
    return _run(
        elss_corpus=0.0,
        non_mf_equity_corpus=0.0,
        mf_corpus=CORPUS,
        human_override=_prefs(
            subgroup_emphasis=emphasis,
            asset_class_requested=class_mix or _class_mix_for(emphasis),
        ),
    )


class TestArbitrageIsALongTermSubgroup:
    """§4. `arbitrage` is offered on the screen and has 182 fundable funds but
    was absent from STEP4_SUBGROUPS, so the long-term step could not write to
    it: an 8% pin produced no `arbitrage` key at all and handed the whole debt
    residual to `arbitrage_plus_income`."""

    def test_arbitrage_is_a_subgroup_step_4_may_write(self):
        from asset_allocation_pydantic.tables import STEP4_SUBGROUPS

        assert "arbitrage" in STEP4_SUBGROUPS

    def test_a_pin_lands_rupees_in_arbitrage(self):
        out = _distribution(
            arbitrage=8.0,
            arbitrage_plus_income=22.0,
            low_beta_equities=40.0,
            us_equities=22.0,
            gold_commodities=8.0,
        )

        assert _subgroups(out).get("arbitrage", 0) == pytest.approx(
            0.08 * CORPUS, abs=1000
        )

    def test_with_no_preference_nothing_routes_to_arbitrage(self):
        """Prozpr's own recommendation is unchanged — plain arbitrage is a
        short/medium instrument and the engine still will not pick it."""
        out = _run(elss_corpus=0.0, non_mf_equity_corpus=0.0, mf_corpus=CORPUS)

        assert _subgroups(out).get("arbitrage", 0) == 0


def _lt_of(class_mix=None, **emphasis):
    """The long-term step's own numbers for a complete distribution, read off
    the trace so `residual_debt_corpus` is available to compare against."""
    from practical_asset_allocation.pipeline import run_practical_allocation

    trace: dict = {}
    run_practical_allocation(
        make_practical_input(
            elss_corpus=0.0,
            non_mf_equity_corpus=0.0,
            mf_corpus=CORPUS,
            human_override=_prefs(
                subgroup_emphasis=emphasis,
                asset_class_requested=class_mix or _class_mix_for(emphasis),
            ),
        ),
        trace,
    )
    return trace["step4_long_term"]["long_term_subgroup_amounts"], trace[
        "step4_long_term"
    ]["residual_debt_corpus"]


_DEBT_ROWS = ("short_debt", "arbitrage", "arbitrage_plus_income")

# A complete distribution with a live multi-asset sleeve: the sleeve draws 25%
# of its 20% = 5% of total out of the debt class, so the pure-debt rows sum to
# 30 - 5 = 25 and the residual is exactly what they asked for.
_WITH_SLEEVE = dict(
    multi_asset=20.0,
    low_beta_equities=30.0,
    us_equities=17.0,
    gold_commodities=8.0,
)

# 60/30/10 with the debt bar stated but no debt ROW named — the partial-pin
# shape, which must keep today's default routing.
_NO_DEBT_ROW_MIX = {"equity": 60.0, "debt": 30.0, "others": 10.0}


class TestDebtResidualSplitsProRata:
    """§7. With no carve-outs, long-term is the whole corpus: the customer's
    pure-debt rows sum to `D - 0.25*M`, which IS the residual. Pro-rata
    therefore reproduces their numbers exactly.

    Amends D-A5: a debt row now lands AT its ask, not at-or-above it."""

    def test_two_named_rows_land_at_their_asks_not_thirty_zero(self):
        amts, residual = _lt_of(short_debt=10.0, arbitrage_plus_income=15.0, **_WITH_SLEEVE)

        assert amts["short_debt"] == pytest.approx(0.10 * CORPUS, abs=1000)
        assert amts["arbitrage_plus_income"] == pytest.approx(0.15 * CORPUS, abs=1000)
        assert amts["short_debt"] + amts["arbitrage_plus_income"] == residual

    def test_three_named_rows_split_pro_rata(self):
        amts, residual = _lt_of(
            short_debt=10.0, arbitrage=5.0, arbitrage_plus_income=10.0, **_WITH_SLEEVE
        )

        assert amts["short_debt"] == pytest.approx(0.10 * CORPUS, abs=1000)
        assert amts["arbitrage"] == pytest.approx(0.05 * CORPUS, abs=1000)
        assert amts["arbitrage_plus_income"] == pytest.approx(0.10 * CORPUS, abs=1000)
        assert sum(amts[sg] for sg in _DEBT_ROWS) == residual

    def test_one_named_row_still_takes_the_whole_residual(self):
        amts, residual = _lt_of(short_debt=25.0, **_WITH_SLEEVE)

        assert amts["short_debt"] == residual
        assert amts["arbitrage_plus_income"] == 0
        assert amts["arbitrage"] == 0

    def test_no_debt_preference_still_routes_to_arbitrage_plus_income(self):
        """Unchanged default: name no debt row and the residual lands where it
        always did. The 30% debt bar is stated but unallocated to a row."""
        amts, residual = _lt_of(
            _NO_DEBT_ROW_MIX,
            low_beta_equities=40.0,
            us_equities=20.0,
            gold_commodities=10.0,
        )

        assert amts["arbitrage_plus_income"] == residual
        assert amts["short_debt"] == 0 and amts["arbitrage"] == 0

    def test_an_excluded_row_among_named_rows_takes_nothing(self):
        """D-B5 reroute is unchanged: an excluded row is never a pro-rata
        participant, and its share redistributes to the rows that remain."""
        amts, residual = _lt_of(
            short_debt=0.0, arbitrage_plus_income=25.0, **_WITH_SLEEVE
        )

        assert amts["short_debt"] == 0
        assert amts["arbitrage_plus_income"] == residual

    def test_the_debt_rows_always_sum_to_the_residual(self):
        """Conservation, across every shape the split can take."""
        for case in (
            dict(short_debt=10.0, arbitrage_plus_income=15.0, **_WITH_SLEEVE),
            dict(short_debt=10.0, arbitrage=5.0, arbitrage_plus_income=10.0, **_WITH_SLEEVE),
            dict(short_debt=25.0, **_WITH_SLEEVE),
            dict(low_beta_equities=40.0, us_equities=20.0, gold_commodities=10.0,
                 class_mix=_NO_DEBT_ROW_MIX),
            dict(short_debt=0.0, arbitrage_plus_income=25.0, **_WITH_SLEEVE),
        ):
            amts, residual = _lt_of(case.pop("class_mix", None), **case)
            assert sum(amts[sg] for sg in _DEBT_ROWS) == residual, case

    def test_the_remainder_lands_on_the_largest_named_row(self):
        """Amounts are rounded to ₹100, so the rupees that will not divide three
        ways go to the biggest ask rather than being dropped."""
        amts, residual = _lt_of(
            short_debt=3.0, arbitrage=3.0, arbitrage_plus_income=19.0, **_WITH_SLEEVE
        )

        assert sum(amts[sg] for sg in _DEBT_ROWS) == residual
        assert amts["arbitrage_plus_income"] >= amts["short_debt"]
        assert amts["arbitrage_plus_income"] >= amts["arbitrage"]


class TestTheExactCompleteDistribution:
    """§7's measured case. Every row must land at its ask — `short_debt` landed
    at ₹0 against a 10% ask before the pro-rata split, with no warning of any
    kind (`sleeve_clamped=False, pins_scaled=False, shortfall_reason=None`)."""

    _ASK = {
        "multi_asset": 20.0,
        "gold_commodities": 8.0,
        "arbitrage_plus_income": 15.0,
        "short_debt": 10.0,
        "low_beta_equities": 20.0,
        "medium_beta_equities": 15.0,
        "us_equities": 12.0,
    }

    def test_every_row_lands_at_its_ask(self):
        got = _subgroups(_distribution(**self._ASK))

        for sg, pct in self._ASK.items():
            assert got.get(sg, 0) == pytest.approx(pct / 100.0 * CORPUS, abs=1000), sg

    def test_the_whole_corpus_is_still_spent(self):
        got = _subgroups(_distribution(**self._ASK))

        assert sum(got.values()) == pytest.approx(CORPUS, abs=1000)

    def test_nothing_was_scaled(self):
        """A distribution that adds up must not be trimmed. The suspension note
        (§9) may still be present — that is a different fact, and this profile
        does have an emergency-fund need to suspend."""
        out = _distribution(**self._ASK)

        assert out.human_override_applied is not None
        reason = out.human_override_applied.shortfall_reason or ""
        assert "scaled proportionally" not in reason
        assert "reduced to the largest" not in reason
        assert "asked" not in reason, "no class gap should be reported"


# ---------------------------------------------------------------------------
# §9 — say that the buffer was suspended
# ---------------------------------------------------------------------------


def _reason(**overrides):
    # `emergency_fund_needed` is stated on every call: the model defaults it to
    # True while the app-side input builder hardcodes False, so an implicit
    # value would test neither the engine's default nor production's.
    overrides.setdefault("emergency_fund_needed", False)
    out = _run(elss_corpus=0.0, non_mf_equity_corpus=0.0, mf_corpus=CORPUS, **overrides)
    applied = out.human_override_applied
    return "" if applied is None else (applied.shortfall_reason or "")


_A_PREFERENCE = dict(human_override=_prefs(subgroup_emphasis={"low_beta_equities": 20.0}))


class TestTheSuspensionIsDisclosed:
    """`human_override`'s emergency guard is gated on `emergency_planned > 0`,
    which under §3 is always 0 — so it can never fire for exactly the population
    it was written for. What is missing is a REPLACEMENT message.

    It is qualitative, with no amounts: saying "we set aside nothing where we'd
    have set aside ₹6,00,000" would mean running step 1 to learn the ₹6,00,000,
    and §3 is explicit that steps 1-3 genuinely do not run."""

    def test_nothing_is_said_when_no_carve_out_was_at_risk(self):
        """The reference profile: no emergency need, positive NFA, no near-term
        goal. Nothing was given up, so there is nothing to disclose."""
        assert "no longer" not in _reason(**_A_PREFERENCE)

    def test_nothing_is_said_without_a_preference(self):
        """No preference means no suspension — and `apply_human_override` is a
        strict no-op there anyway."""
        assert _reason(emergency_fund_needed=True) == ""

    def test_the_engine_default_is_not_what_production_sends(self):
        """Guard on the fixture above: `AllocationInput` defaults the flag to
        True, the app-side builder hardcodes False. If they ever converge, the
        explicit values in these tests stop being load-bearing and someone
        should know."""
        from asset_allocation_pydantic.models import AllocationInput

        assert AllocationInput.model_fields["emergency_fund_needed"].default is True

    def test_an_emergency_fund_need_is_disclosed(self):
        reason = _reason(emergency_fund_needed=True, **_A_PREFERENCE)

        assert "emergency fund" in reason
        assert "no longer" in reason

    def test_a_near_term_goal_is_disclosed(self):
        reason = _reason(goals=[_goal(18)], **_A_PREFERENCE)

        assert "five years" in reason

    def test_a_goal_beyond_five_years_is_not_a_near_term_goal(self):
        """60 months is the boundary step 4 itself uses — a goal ON it is
        funded long-term and loses nothing."""
        assert "five years" not in _reason(goals=[_goal(60)], **_A_PREFERENCE)

    def test_a_liability_offset_is_disclosed_on_its_own(self):
        """§3.4: the NFA offset is a distinct fact from the emergency fund and
        is computed regardless of `emergency_fund_needed`. A leveraged customer
        with no emergency need must be told about the offset and nothing else."""
        reason = _reason(
            emergency_fund_needed=False, net_financial_assets=-800_000.0, **_A_PREFERENCE
        )

        assert "borrowings" in reason
        assert "emergency fund" not in reason

    def test_all_three_are_disclosed_together(self):
        reason = _reason(
            emergency_fund_needed=True,
            net_financial_assets=-800_000.0,
            goals=[_goal(18)],
            **_A_PREFERENCE,
        )

        assert "emergency fund" in reason
        assert "five years" in reason
        assert "borrowings" in reason

    def test_it_composes_with_the_other_disclosures(self):
        """`shortfall_reason` carries every note; the suspension must join them
        rather than replace them."""
        reason = _reason(
            emergency_fund_needed=True,
            human_override=_prefs(
                asset_class_requested={"equity": 20.0, "debt": 70.0, "others": 10.0},
                subgroup_emphasis={"multi_asset": 60.0},
            ),
        )

        assert "multi-asset" in reason and "emergency fund" in reason


class TestCarveOutsAtRisk:
    """§9.1. The same three conditions, read off the profile — one source of
    truth with the disclosure above, so the warning shown BEFORE the customer
    commits and the record attached AFTER cannot disagree."""

    def _at_risk(self, **overrides):
        from practical_asset_allocation.pipeline import carve_outs_at_risk

        overrides.setdefault("emergency_fund_needed", False)
        return carve_outs_at_risk(
            make_practical_input(
                elss_corpus=0.0, non_mf_equity_corpus=0.0, mf_corpus=CORPUS, **overrides
            )
        )

    def test_empty_for_the_reference_profile(self):
        assert self._at_risk() == []

    def test_emergency_fund_alone(self):
        assert self._at_risk(emergency_fund_needed=True) == ["emergency_fund"]

    def test_liability_offset_alone(self):
        """The case §3.4 exists for, and the one a single boolean flag would
        have got wrong."""
        assert self._at_risk(
            emergency_fund_needed=False, net_financial_assets=-800_000.0
        ) == ["liability_offset"]

    def test_near_term_goals_alone(self):
        assert self._at_risk(goals=[_goal(18), _goal(120, name="Retirement")]) == [
            "near_term_goals"
        ]

    def test_all_three_together(self):
        assert self._at_risk(
            emergency_fund_needed=True,
            net_financial_assets=-800_000.0,
            goals=[_goal(18)],
        ) == ["emergency_fund", "near_term_goals", "liability_offset"]

    def test_it_does_not_depend_on_a_preference_being_set(self):
        """It is a WARNING shown before the customer commits, so it is computed
        from the profile alone — there is no saved preference yet."""
        assert self._at_risk(emergency_fund_needed=True) == self._at_risk(
            emergency_fund_needed=True, **_A_PREFERENCE
        )


# ---------------------------------------------------------------------------
# Drift on a complete distribution (code review 2026-09-15)
# ---------------------------------------------------------------------------


class TestDriftIsSpreadAcrossPinnedRows:
    """With a COMPLETE distribution every equity row is either pinned or
    excluded, so the drift reconciliation's "prefer an engine-filled row"
    fallback has no engine-filled row to prefer — what the code documents as a
    degenerate case becomes the normal one.

    The sleeve cannot take the money back: it is spare equity precisely BECAUSE
    a different class room clamped the sleeve, and growing it would draw debt
    the customer's debt pins have already claimed (measured: absorbing ₹1,60,000
    of spare equity costs the two debt rows ₹55,400). So the drift must land on
    pinned equity rows — but spread in proportion, not dumped whole on the
    largest, which put one row 8.4% above what the customer typed.
    """

    # The screen's own recommendation for a profile with a near-term goal: the
    # case where the debt room clamps the sleeve and frees equity.
    _PINS = {
        "multi_asset": 42.9,
        "arbitrage": 10.0,
        "arbitrage_plus_income": 14.5,
        "gold_commodities": 4.7,
        "low_beta_equities": 9.4,
        "medium_beta_equities": 9.4,
        "us_equities": 9.1,
    }
    _MIX = {"equity": 55.8, "debt": 35.2, "others": 9.0}
    # Blanks sent as explicit zeros — what makes the distribution COMPLETE, and
    # what removes every engine-filled equity row the drift used to land on.
    _BLANKS = {
        "high_beta_equities": 0.0,
        "value_equities": 0.0,
        "sector_equities": 0.0,
        "short_debt": 0.0,
    }

    def _equity_rows(self, complete=True):
        from practical_asset_allocation.pipeline import run_practical_allocation

        pins = {**self._BLANKS, **self._PINS} if complete else dict(self._PINS)
        trace: dict = {}
        run_practical_allocation(
            make_practical_input(
                elss_corpus=0.0,
                non_mf_equity_corpus=0.0,
                mf_corpus=CORPUS,
                emergency_fund_needed=False,
                human_override=_prefs(
                    subgroup_emphasis=pins, asset_class_requested=self._MIX
                ),
            ),
            trace,
        )
        return trace["step4_long_term"]

    def test_partial_pins_still_land_the_drift_on_an_engine_filled_row(self):
        """The control, and the behaviour that must not change. Leave a row
        blank-as-omitted and the engine still owns it, so D-A1 holds the old
        way: the drift goes there and no pin is touched."""
        lt = self._equity_rows(complete=False)
        amounts = lt["equity_subgroup_amounts"]

        assert amounts["high_beta_equities"] > 0, "an engine-filled row must exist"
        for sg in ("low_beta_equities", "medium_beta_equities", "us_equities"):
            assert amounts[sg] == pytest.approx(self._PINS[sg] / 100.0 * CORPUS, abs=100)

    def test_the_drift_is_spread_in_proportion_not_dumped_on_one_row(self):
        """The regression this exists for. Dumped whole, ₹1,60,000 landed on
        `low_beta_equities` alone — 8.5% above what the customer typed, while
        its two neighbours landed exactly. Spread, each row carries its own
        proportional share to within a rounding step."""
        amounts = self._equity_rows()["equity_subgroup_amounts"]
        asks = {
            sg: pct / 100.0 * CORPUS
            for sg, pct in self._PINS.items()
            if sg.endswith("_equities")
        }

        drift = sum(amounts[sg] - ask for sg, ask in asks.items())
        assert drift > 0, "fixture must actually produce drift"
        for sg, ask in asks.items():
            fair = drift * ask / sum(asks.values())
            assert abs((amounts[sg] - ask) - fair) <= 100, (
                f"{sg} carries {amounts[sg] - ask:,.0f} of the {drift:,.0f} drift, "
                f"its proportional share is {fair:,.0f}"
            )

    def test_an_excluded_row_is_never_used_as_a_drift_sink(self):
        """A zero is a hard exclusion. Spreading must not quietly refill one."""
        amounts = self._equity_rows()["equity_subgroup_amounts"]

        for sg in self._BLANKS:
            if sg.endswith("_equities"):
                assert amounts[sg] == 0, sg

    def test_the_equity_pool_is_still_spent_exactly(self):
        lt = self._equity_rows()

        assert sum(lt["equity_subgroup_amounts"].values()) == lt[
            "residual_equity_corpus_final"
        ]

    def test_the_debt_rows_are_untouched_by_the_spread(self):
        """Guard: fixing equity must not reach into debt."""
        lt = self._equity_rows()
        amounts = lt["long_term_subgroup_amounts"]

        for sg in ("arbitrage", "arbitrage_plus_income"):
            assert amounts[sg] == pytest.approx(
                self._PINS[sg] / 100.0 * CORPUS, abs=10_000
            ), sg
