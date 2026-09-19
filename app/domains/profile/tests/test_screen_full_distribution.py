"""Spec 2026-09-15 §5/§6/§8 — the preferences screen speaks a COMPLETE distribution.

§5: `multi_asset` is attributed 65/25/10 for the class-fit check, the way the
engine actually carves it — not wholly as equity.
§6: a blank row is a zero, a zero is a valid entry, and it must round-trip.
§8: the recommendation the screen shows keeps the carve-outs (it is Prozpr's
actual advice), and accepting it verbatim must reproduce materially the same
per-subgroup allocation. That is the proof §7's pro-rata debt split is complete.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.profile.services.preference_save_service import _build_ctx
from app.domains.profile.services.screen_preference_service import (
    ScreenPreferenceError,
    resolve_screen_preferences,
    subcategory_catalog,
)

ensure_ai_agents_path()
from practical_asset_allocation.human_override import CLASS_OF  # noqa: E402

CORPUS = 20_000_000.0


def _goal(name, months, amount):
    return SimpleNamespace(
        status="ACTIVE",
        target_date=date.today() + timedelta(days=30 * months),
        goal_type="other",
        goal_name=name,
        present_value_amount=amount,
    )


def _user(goals=None):
    """A profile complete enough for the real input builder — the screen's own
    path, not a hand-built engine input."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        date_of_birth=date(1986, 1, 1),
        personal_finance_profile=SimpleNamespace(
            financial_assets=CORPUS,
            monthly_household_expense=100_000,
            financial_liabilities_excl_mortgage=0,
            annual_income=2_000_000,
        ),
        financial_goals=list(goals or []),
        portfolios=[],
    )


# A near-term goal is the carve-out this path can actually produce:
# `emergency_fund_needed` is hardcoded False in the input builder (not yet a DB
# column), so a 17-month goal is what makes the neutral run's short-term bucket
# non-zero — and therefore what makes §8's round-trip claim testable.
_NEAR_TERM_GOALS = [_goal("Car", 18, 2_000_000), _goal("Retirement", 120, 10_000_000)]


def _screen_input(goals=None):
    from app.domains.practical_asset_allocation.services.paa_engine.input_builder import (
        build_practical_allocation_input_for_user,
    )

    inp, _ = build_practical_allocation_input_for_user(
        _build_ctx(_user(goals)), apply_saved_preferences=False
    )
    return inp


def _neutral_run(goals=None):
    """What `screen_read_model` computes the recommendation and catalog from:
    a run with `apply_saved_preferences=False` — i.e. WITH the carve-outs."""
    from practical_asset_allocation.pipeline import run_practical_allocation

    inp = _screen_input(goals)
    return inp, run_practical_allocation(inp)


def _accept_the_recommendation(inp, recommended):
    """Save the screen's own figures back, verbatim: the recommended class mix
    plus one pin per settable row at its recommended share."""
    from practical_asset_allocation.human_override import HumanOverridePreferences
    from practical_asset_allocation.pipeline import run_practical_allocation

    resolved = resolve_screen_preferences(
        _class_mix(recommended),
        [
            {"subgroup": c.id, "pct_of_total": c.recommended_pct_of_total}
            for c in subcategory_catalog(recommended)
        ],
    )
    return run_practical_allocation(
        inp.model_copy(
            update={
                "human_override": HumanOverridePreferences(
                    asset_class_requested=resolved.asset_class_requested,
                    subgroup_emphasis=resolved.subgroup_emphasis,
                )
            }
        )
    )


def _class_mix(out) -> dict:
    rec = out.asset_class_breakdown.recommended
    return {
        "equity": rec.equity_total_pct,
        "debt": rec.debt_total_pct,
        "others": rec.others_total_pct,
    }


def _subgroups(out) -> dict:
    return {r.subgroup: r.total for r in out.aggregated_subgroups}


# ---------------------------------------------------------------------------
# §5 — multi_asset is attributed across all three classes
# ---------------------------------------------------------------------------


class TestMultiAssetIsAttributed:
    """`CLASS_OF["multi_asset"]` is "equity", so validation charged the whole
    ask against the equity bar while the engine carved it 65/25/10. The check
    must attribute it the way the engine does."""

    def test_a_multi_asset_ask_that_fits_all_three_classes_validates(self):
        # 20% consumes 13 equity / 5 debt / 2 others against 78/14/8.
        r = resolve_screen_preferences(
            {"equity": 78, "debt": 14, "others": 8},
            [{"subgroup": "multi_asset", "pct_of_total": 20}],
        )
        assert r.subgroup_emphasis["multi_asset"] == pytest.approx(20.0)

    def test_the_debt_slice_can_fail_the_debt_class(self):
        # Same 20% ask, but its 5% debt slice does not fit a 4% debt bar.
        with pytest.raises(ScreenPreferenceError) as ei:
            resolve_screen_preferences(
                {"equity": 88, "debt": 4, "others": 8},
                [{"subgroup": "multi_asset", "pct_of_total": 20}],
            )
        assert "debt" in str(ei.value)

    def test_it_is_no_longer_charged_wholly_to_equity(self):
        # 20% against a 15% equity bar: rejected while multi_asset counted as
        # 100% equity, accepted now that only its 13% equity slice does.
        r = resolve_screen_preferences(
            {"equity": 15, "debt": 50, "others": 35},
            [{"subgroup": "multi_asset", "pct_of_total": 20}],
        )
        assert r.subgroup_emphasis["multi_asset"] == pytest.approx(20.0)

    def test_the_slices_come_from_the_engine_not_a_literal(self):
        """Read the proportions off `multi_asset_composition` so validation and
        the engine's carve cannot drift apart."""
        from asset_allocation_pydantic.tables import (
            DEFAULT_MULTI_ASSET_COMPOSITION_PCTS,
        )
        from app.domains.profile.services import screen_preference_service as svc

        eq, dt, ot = DEFAULT_MULTI_ASSET_COMPOSITION_PCTS
        assert svc._MULTI_ASSET_SLICES == {
            "equity": eq / 100.0,
            "debt": dt / 100.0,
            "others": ot / 100.0,
        }

    def test_the_sleeve_still_shares_the_equity_bar_with_equity_rows(self):
        # 20% sleeve (13 equity) + 10% large-cap = 23 > a 20% equity bar.
        with pytest.raises(ScreenPreferenceError) as ei:
            resolve_screen_preferences(
                {"equity": 20, "debt": 70, "others": 10},
                [
                    {"subgroup": "multi_asset", "pct_of_total": 20},
                    {"subgroup": "low_beta_equities", "pct_of_total": 10},
                ],
            )
        assert "equity" in str(ei.value)


# ---------------------------------------------------------------------------
# §6 — a zero is a valid entry and must round-trip
# ---------------------------------------------------------------------------


class TestZerosAreValidEntries:
    """A blank row means zero, which is exactly the engine's reading of 0 in
    `subgroup_emphasis`: a hard exclusion."""

    def test_a_zero_pin_is_accepted(self):
        r = resolve_screen_preferences(
            {"equity": 72, "debt": 18, "others": 10},
            [{"subgroup": "short_debt", "pct_of_total": 0}],
        )
        assert r.subgroup_emphasis["short_debt"] == 0.0

    def test_a_zero_consumes_no_class_budget(self):
        # Debt is fully spoken for by arbitrage+income; a zero on short_debt
        # must not push the class over.
        r = resolve_screen_preferences(
            {"equity": 72, "debt": 18, "others": 10},
            [
                {"subgroup": "arbitrage_plus_income", "pct_of_total": 18},
                {"subgroup": "short_debt", "pct_of_total": 0},
            ],
        )
        assert r.subgroup_emphasis["short_debt"] == 0.0
        assert r.subgroup_emphasis["arbitrage_plus_income"] == pytest.approx(18.0)

    def test_a_negative_pin_is_still_rejected(self):
        with pytest.raises(ScreenPreferenceError):
            resolve_screen_preferences(
                {"equity": 72, "debt": 18, "others": 10},
                [{"subgroup": "short_debt", "pct_of_total": -1}],
            )

    def test_a_complete_distribution_of_zeros_and_numbers_resolves(self):
        """Pins arrive COMPLETE — one entry per settable category, blanks sent
        as explicit zeros."""
        from app.domains.profile.services.screen_preference_service import (
            _settable_subcategory_ids,
        )

        asked = {"low_beta_equities": 72.0, "arbitrage_plus_income": 18.0,
                 "gold_commodities": 10.0}
        pins = [
            {"subgroup": sg, "pct_of_total": asked.get(sg, 0.0)}
            for sg in _settable_subcategory_ids()
        ]
        r = resolve_screen_preferences({"equity": 72, "debt": 18, "others": 10}, pins)

        assert set(r.subgroup_emphasis) == set(_settable_subcategory_ids())
        assert r.subgroup_emphasis["sector_equities"] == 0.0
        assert r.subgroup_emphasis["low_beta_equities"] == pytest.approx(72.0)


class TestZerosRoundTripOnGet:
    """`screen_read_model` filtered zeros out of `saved.pins`, so an emptied
    row came back showing the engine's number instead of the customer's zero."""

    def _saved_pins(self, resolved_targets):
        import asyncio

        from app.domains.profile.services import screen_preference_service as svc

        async def fake_active(db, user_id):
            return SimpleNamespace(
                asset_class_requested={"equity": 72.0, "debt": 18.0, "others": 10.0},
                resolved_targets=resolved_targets,
                activated_at=None,
            )

        original = svc.active_preference_row
        svc.active_preference_row = fake_active
        try:
            resp = asyncio.run(svc.screen_read_model(None, _user()))
        finally:
            svc.active_preference_row = original
        return {p.subgroup: p.pct_of_total for p in resp.saved.pins}

    def test_a_saved_zero_comes_back_as_zero(self):
        pins = self._saved_pins({"low_beta_equities": 25.0, "short_debt": 0.0})

        assert pins["short_debt"] == 0.0
        assert pins["low_beta_equities"] == pytest.approx(25.0)

    def test_an_omitted_row_stays_omitted(self):
        """An absent row still means "engine decides" — the zero and the
        omission are different facts and must not collapse into one."""
        pins = self._saved_pins({"low_beta_equities": 25.0})

        assert "short_debt" not in pins


# ---------------------------------------------------------------------------
# §8 — the recommendation is a fixed point
# ---------------------------------------------------------------------------


class TestTheRecommendationIsAFixedPoint:
    def test_every_debt_row_lands_at_its_ask(self):
        """THE proof that §7 is complete, and the sharpest form of it. Before
        the pro-rata split a named debt row landed at ₹0 while another took the
        whole residual; the recommendation for this profile names two (the
        17-month goal's ₹20L of `arbitrage`, plus `arbitrage_plus_income`), so
        the old rule would have emptied one of them.

        Tight tolerance on purpose: only the catalog's own 1-decimal rounding
        stands between the ask and the placement here."""
        inp, recommended = _neutral_run(_NEAR_TERM_GOALS)
        preferred = _accept_the_recommendation(inp, recommended)

        catalog = {c.id: c.recommended_pct_of_total for c in subcategory_catalog(recommended)}
        placed = _subgroups(preferred)
        named = {sg: pct for sg, pct in catalog.items()
                 if pct > 0 and CLASS_OF[sg] == "debt"}

        assert len(named) >= 2, "fixture must name more than one debt row"
        for sg, pct in named.items():
            assert placed.get(sg, 0.0) == pytest.approx(
                pct / 100.0 * recommended.grand_total, abs=10_000
            ), sg

    def test_accepting_the_recommendation_reproduces_the_allocation(self):
        """The customer taps nothing, saves what the screen already shows, and
        their rupees must not move materially.

        The 1%-of-corpus tolerance is not slack — it is two measured, additive
        precision losses, both OUTSIDE this change's scope (2026-09-15):

        * `phase2_asset_class_pcts` rounds a requested class split to INTEGER
          percentages, so a 35.1999% debt ask is run as 35% — ₹39,980 on ₹2cr.
          The sleeve draws debt at 0.25, so that shortfall is amplified 4× into
          a ₹159,920 smaller sleeve. 87% of the gap.
        * `subcategory_catalog` rounds each row to 1 decimal, and a row that
          rounds UP eats the sleeve's room. 13% of the gap.

        The freed equity has nowhere neutral to land — under a complete
        distribution every equity row is either pinned or excluded — so a pin
        must absorb it. Since the code review it is SPREAD in proportion rather
        than dumped on the largest row, which is why no single equity row runs
        more than ~0.28pp high (it was 0.79pp on `low_beta_equities`). The
        sleeve keeps the whole ~0.92pp because it is the residual instrument
        and cannot grow without stealing from the debt pins.

        None of this is §7: the debt rows land exactly (test above)."""
        inp, recommended = _neutral_run(_NEAR_TERM_GOALS)
        preferred = _accept_the_recommendation(inp, recommended)

        before, after = _subgroups(recommended), _subgroups(preferred)
        tol = 0.01 * recommended.grand_total
        for sg in set(before) | set(after):
            assert after.get(sg, 0.0) == pytest.approx(before.get(sg, 0.0), abs=tol), sg

    def test_no_row_the_recommendation_funded_is_emptied(self):
        """The failure mode that matters to a customer: a row shown with money
        against it coming back at zero. ₹0 against a 10% ask is what §7 existed
        to stop, and no tolerance can hide it."""
        inp, recommended = _neutral_run(_NEAR_TERM_GOALS)
        preferred = _accept_the_recommendation(inp, recommended)

        after = _subgroups(preferred)
        for sg, amount in _subgroups(recommended).items():
            if amount > 0:
                assert after.get(sg, 0.0) > 0, sg

    def test_the_bucket_attribution_changes_even_though_the_money_does_not(self):
        """§8's honest caveat, pinned. The 17-month goal's ₹20L was short-term
        before and is long-term after — the reserve label goes, the rupees
        stay. If this ever stops differing, the round-trip tests above have
        quietly stopped exercising the carve-out difference at all."""
        inp, recommended = _neutral_run(_NEAR_TERM_GOALS)
        preferred = _accept_the_recommendation(inp, recommended)

        before = {b.bucket: b.allocated_amount for b in recommended.bucket_allocations}
        after = {b.bucket: b.allocated_amount for b in preferred.bucket_allocations}

        assert before["short_term"] == pytest.approx(2_000_000, abs=1000)
        assert after["short_term"] == 0
        assert after["long_term"] == pytest.approx(CORPUS, abs=1000)

    def test_a_complete_distribution_raises_no_sleeve_warning(self):
        """§8.1. Accepting the recommendation verbatim must not warn the
        customer that their multi-asset choice had to be cut.

        NOT the mechanism the spec describes: the clamp is already gated on a
        strict reduction, so equality never fired. The sleeve really is trimmed
        — by 0.161pp of the portfolio here — because the catalog shows each row
        to one decimal and three equity rows round UP. The disclosure floor
        absorbs exactly that."""
        inp, recommended = _neutral_run()
        preferred = _accept_the_recommendation(inp, recommended)

        applied = preferred.human_override_applied
        assert applied is not None
        assert applied.shortfall_reason is None, applied.shortfall_reason

    def test_a_trim_bigger_than_the_screens_precision_is_still_disclosed(self):
        """The floor must not become a blanket silence. With a near-term goal
        the trim is 0.924pp — five times the display noise, caused by
        `phase2_asset_class_pcts` rounding a requested class split to INTEGER
        percentages (₹39,980 of debt, amplified 4x by the sleeve's 0.25 slice).
        That is a real shortfall and the customer is still told."""
        inp, recommended = _neutral_run(_NEAR_TERM_GOALS)
        preferred = _accept_the_recommendation(inp, recommended)

        applied = preferred.human_override_applied
        assert applied is not None
        assert "multi-asset" in (applied.shortfall_reason or "")


# ---------------------------------------------------------------------------
# Catalog invariant (§11) and the ELSS precondition
# ---------------------------------------------------------------------------


def test_settable_rows_cover_the_whole_portfolio():
    """Frontend-facing. The screen derives its class bar from these figures, so
    a catalog that stops covering the whole portfolio silently mis-targets every
    budget on it. The 0.6 tolerance is per-row rounding; anything larger means
    `elss_corpus` has become non-zero on this path."""
    _, out = _neutral_run()

    total = sum(c.recommended_pct_of_total for c in subcategory_catalog(out))
    assert total == pytest.approx(100.0, abs=0.6)


def test_the_screens_input_path_carries_no_elss():
    """§11. Percentages are of TOTAL while the pool they are placed against is
    `total - elss`, so every exactness claim here holds only while ELSS is zero.
    The lumpsum deficit-fill path already supplies real ELSS via a `CorpusPin`,
    so the wiring to break this exists in the codebase today."""
    inp = _screen_input()

    assert inp.elss_corpus == 0.0
    assert inp.non_mf_equity_corpus == 0.0


# ---------------------------------------------------------------------------
# §9.1 — carve_outs_at_risk on the GET response
# ---------------------------------------------------------------------------


class TestCarveOutsAtRiskOnTheGetResponse:
    """The disclosure in §9 is a RECORD, attached after the run. The screen also
    needs a WARNING, shown before the customer commits, while they can still
    change their mind. `ScreenPreferenceGetResponse` carries no profile flags,
    so the field is added to it."""

    def _get(self, goals=None):
        import asyncio

        from app.domains.profile.services import screen_preference_service as svc

        async def no_saved_row(db, user_id):
            return None

        original = svc.active_preference_row
        svc.active_preference_row = no_saved_row
        try:
            return asyncio.run(svc.screen_read_model(None, _user(goals)))
        finally:
            svc.active_preference_row = original

    def test_it_is_empty_when_nothing_is_at_risk(self):
        """The screen then renders nothing. `emergency_fund_needed` is hardcoded
        False by the input builder and this profile has positive NFA and no
        near-term goal, so none of the three apply."""
        assert self._get().carve_outs_at_risk == []

    def test_a_near_term_goal_puts_goals_at_risk(self):
        assert self._get(_NEAR_TERM_GOALS).carve_outs_at_risk == ["near_term_goals"]

    def test_a_long_dated_goal_alone_puts_nothing_at_risk(self):
        assert self._get([_goal("Retirement", 120, 10_000_000)]).carve_outs_at_risk == []

    def test_the_field_is_optional_on_the_wire(self):
        """The frontend ships its carve-out panel ahead of this and degrades to
        showing nothing, so the field must not be required to construct the
        response."""
        from app.domains.profile.schemas import ScreenPreferenceGetResponse

        resp = ScreenPreferenceGetResponse(
            recommendation={"class_mix": {"equity": 60.0, "debt": 30.0, "others": 10.0}},
            subcategories=[],
        )
        assert resp.carve_outs_at_risk == []

    def test_an_unknown_value_is_rejected(self):
        """The three values are a closed set — the frontend switches on them."""
        import pydantic

        from app.domains.profile.schemas import ScreenPreferenceGetResponse

        with pytest.raises(pydantic.ValidationError):
            ScreenPreferenceGetResponse(
                recommendation={"class_mix": {"equity": 100.0, "debt": 0.0, "others": 0.0}},
                subcategories=[],
                carve_outs_at_risk=["something_else"],
            )

    def test_it_agrees_with_what_the_run_would_disclose(self):
        """One source of truth (§9/§9.1): the warning shown BEFORE the customer
        commits and the record attached AFTER must name the same facts."""
        from practical_asset_allocation.pipeline import carve_outs_at_risk

        warned = self._get(_NEAR_TERM_GOALS).carve_outs_at_risk
        recorded = carve_outs_at_risk(_screen_input(_NEAR_TERM_GOALS))

        assert warned == recorded
