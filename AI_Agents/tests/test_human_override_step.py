"""Unit tests for practical_asset_allocation.human_override (spec §3)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# AI_Agents/tests is a package (__init__.py), so sibling test modules are not
# importable bare under pytest — same shim as test_rebal_detector_eval.py.
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))


class TestPreferencesModel:
    def test_empty_model_is_empty(self):
        from practical_asset_allocation.human_override import HumanOverridePreferences

        assert HumanOverridePreferences().is_empty() is True

    def test_class_target_makes_it_non_empty(self):
        from practical_asset_allocation.human_override import HumanOverridePreferences

        p = HumanOverridePreferences(
            asset_class_requested={"equity": 80.0, "debt": 15.0, "others": 5.0}
        )
        assert p.is_empty() is False

    def test_class_mix_must_cover_all_three_and_sum_100(self):
        from practical_asset_allocation.human_override import HumanOverridePreferences

        with pytest.raises(ValueError):
            HumanOverridePreferences(asset_class_requested={"equity": 80.0})
        with pytest.raises(ValueError):
            HumanOverridePreferences(
                asset_class_requested={"equity": 80.0, "debt": 30.0, "others": 5.0}
            )

    def test_unknown_subgroup_key_rejected(self):
        from practical_asset_allocation.human_override import HumanOverridePreferences

        with pytest.raises(ValueError):
            HumanOverridePreferences(subgroup_emphasis={"smallcap_funds": 0.0})

    def test_frozen_subgroups_rejected(self):
        from practical_asset_allocation.human_override import HumanOverridePreferences

        with pytest.raises(ValueError):
            HumanOverridePreferences(subgroup_emphasis={"tax_efficient_equities": 0.0})
        with pytest.raises(ValueError):
            HumanOverridePreferences(subgroup_emphasis={"non_mf_equities": 20.0})

    def test_subgroup_exclusions_field_is_rejected(self):
        # Exclusion folded into emphasis (0 = excluded): the old list field
        # must fail loud, not be silently ignored.
        from practical_asset_allocation.human_override import HumanOverridePreferences

        with pytest.raises(ValueError):
            HumanOverridePreferences(subgroup_exclusions=["us_equities"])

    def test_market_cap_target_is_rejected(self):
        # Market-cap folded into subgroup_emphasis (2026-09-04 restructure):
        # the old facet must fail loud, not be silently ignored.
        from practical_asset_allocation.human_override import HumanOverridePreferences

        with pytest.raises(ValueError):
            HumanOverridePreferences(
                market_cap_target={"large": 30.0, "mid": 30.0, "small": 40.0}
            )


def _run_practical(**overrides):
    from practical_asset_allocation.pipeline import run_practical_allocation
    from test_human_override_golden import make_practical_input

    return run_practical_allocation(make_practical_input(**overrides))


def _run_practical_with_prefs(prefs, **overrides):
    """Spec 2026-09-14: a class preference is honoured at phase 2 of the
    practical engine's long-term step, so it rides in on the INPUT — step 6
    (`apply_human_override`) no longer reshapes classes post-hoc."""
    from practical_asset_allocation.pipeline import run_practical_allocation
    from test_human_override_golden import make_practical_input

    return run_practical_allocation(
        make_practical_input(**overrides).model_copy(update={"human_override": prefs})
    )


def _run_practical_with_prefs_traced(prefs, **overrides):
    """As above, plus the long-term step's internal state — what the ENGINE
    decided. Spec 2026-09-14 (Task C1): sub-group pins and exclusions are
    placed at phases 4/5, so the engine's own numbers are the thing to assert;
    step 6 only reports."""
    from practical_asset_allocation.pipeline import run_practical_allocation
    from test_human_override_golden import make_practical_input

    trace: dict = {}
    out = run_practical_allocation(
        make_practical_input(**overrides).model_copy(update={"human_override": prefs}),
        trace=trace,
    )
    return out, trace["step4_long_term"]


def _class_mix_pct(out):
    b = out.asset_class_breakdown.recommended
    return {
        "equity": b.equity_total_pct,
        "debt": b.debt_total_pct,
        "others": b.others_total_pct,
    }


class TestClassTargetReshape:
    def _apply(self, out, requested):
        from practical_asset_allocation.human_override import (
            HumanOverridePreferences,
            apply_human_override,
        )

        prefs = HumanOverridePreferences(asset_class_requested=requested)
        base = _run_practical() if out is None else out
        return apply_human_override(
            base, prefs, base.client_summary and base and base
        )

    def test_none_prefs_is_identity(self):
        from practical_asset_allocation.human_override import apply_human_override

        out = _run_practical()
        reshaped, applied = apply_human_override(out, None, None)
        assert applied is None
        assert reshaped.model_dump(mode="json") == out.model_dump(mode="json")

    # Spec 2026-09-14: the class ask arrives on the engine INPUT (honoured at
    # phase 2 of the long-term step), not via a post-hoc step-6 call.
    def test_equity_up_hits_target_and_conserves_grand_total(self):
        from practical_asset_allocation.human_override import HumanOverridePreferences

        out = _run_practical()
        prefs = HumanOverridePreferences(
            asset_class_requested={"equity": 80.0, "debt": 15.0, "others": 5.0}
        )
        reshaped = _run_practical_with_prefs(prefs)
        applied = reshaped.human_override_applied
        mix = _class_mix_pct(reshaped)
        assert abs(mix["equity"] - 80.0) < 1.5
        assert abs(sum(r.total for r in reshaped.aggregated_subgroups)
                   - sum(r.total for r in out.aggregated_subgroups)) < 500
        # Task C2: the run carries only the has-preference flag + shortfall.
        # The ask is the saved-preference row's; the achieved mix IS `mix`.
        assert applied is not None
        # The rebuilt subgroups view agrees with the reshaped table (spec F1) —
        # not the stale pre-reshape block carried through unchanged.
        long_term_split = next(
            s for s in reshaped.asset_class_breakdown.subgroups.recommended
            if s.bucket == "long_term"
        )
        row_long_term = {
            r.subgroup: r.long_term
            for r in reshaped.aggregated_subgroups
            if r.long_term > 0
        }
        view_long_term = {a.subgroup: a.amount for a in long_term_split.subgroups}
        assert set(view_long_term) == set(row_long_term)
        for sg, amt in view_long_term.items():
            assert abs(amt - row_long_term[sg]) <= 1

    # test_shrink_is_uniform_growth_lands_in_long_term was DELETED (spec
    # 2026-09-14): it asserted the uniform-shrink-factor ALGORITHM of the old
    # step-6 class reshape, which was removed and has no successor. Its
    # concern — growing equity must not eat the emergency bucket — now holds
    # by construction and is pinned by
    # test_phase2_class_override.py::test_85_7_8_lands_exactly_including_debt.

    def test_frozen_rows_never_move(self):
        from practical_asset_allocation.human_override import (
            HumanOverridePreferences,
            apply_human_override,
        )

        out = _run_practical()
        prefs = HumanOverridePreferences(
            asset_class_requested={"equity": 20.0, "debt": 70.0, "others": 10.0}
        )
        reshaped, applied = apply_human_override(out, prefs, None)
        before = {r.subgroup: r.total for r in out.aggregated_subgroups}
        after = {r.subgroup: r.total for r in reshaped.aggregated_subgroups}
        assert after["tax_efficient_equities"] == before["tax_efficient_equities"]
        assert after["non_mf_equities"] == before["non_mf_equities"]

    def test_equity_down_floored_by_frozen_discloses_shortfall(self):
        from practical_asset_allocation.human_override import (
            HumanOverridePreferences,
            apply_human_override,
        )

        # elss 40L + stocks 10L on a 60L corpus → frozen equity ≈ 83% floor.
        out = _run_practical(
            total_corpus=6_000_000.0, mf_corpus=5_000_000.0,
            elss_corpus=4_000_000.0, non_mf_equity_corpus=1_000_000.0,
            net_financial_assets=6_000_000.0,
        )
        prefs = HumanOverridePreferences(
            asset_class_requested={"equity": 10.0, "debt": 80.0, "others": 10.0}
        )
        reshaped, applied = apply_human_override(out, prefs, None)
        # Task C2: achieved is read off the run's own breakdown, not carried.
        assert _class_mix_pct(reshaped)["equity"] > 50.0  # frozen floor kept it high
        assert applied.shortfall_reason is not None

    # test_apply_class_targets_zero_grand_total_returns_tuple was DELETED
    # (spec 2026-09-14): it called the private _apply_class_targets, the
    # old step-6 class reshape, which this phase removed entirely.


class TestSubgroupStages:
    def _apply(self, out, **pref_kwargs):
        from practical_asset_allocation.human_override import (
            HumanOverridePreferences,
            apply_human_override,
        )

        return apply_human_override(
            out, HumanOverridePreferences(**pref_kwargs), None
        )

    # spec 2026-09-14: the ask is an INPUT pin placed at phase 5, and its
    # basis is the WHOLE portfolio (D-A3) — not the "migratable" slice (class
    # minus ELSS, direct stock and the sleeve) that step 6 was allowed to move,
    # and no longer the equity class either. Same basis shift as
    # test_class_target_and_beta_emphasis_compose already documents.
    def test_emphasis_on_beta_subgroup_carries_market_cap_ask(self):
        # "smallcap heavy" resolves app-side to emphasis on high_beta_equities
        # (share of the whole portfolio, not just the beta sleeve).
        from asset_allocation_pydantic.utils import round_to_100
        from practical_asset_allocation.human_override import HumanOverridePreferences

        out, s4 = _run_practical_with_prefs_traced(
            HumanOverridePreferences(subgroup_emphasis={"high_beta_equities": 22.5})
        )
        rows = {r.subgroup: r.total for r in out.aggregated_subgroups}
        assert s4["equity_subgroup_amounts"]["high_beta_equities"] == round_to_100(
            out.grand_total * 0.225
        )
        # And the placement survives to the table the customer reads.
        assert abs(rows.get("high_beta_equities", 0.0) - out.grand_total * 0.225) < 500
        assert abs(sum(rows.values()) - out.grand_total) < 500

    # spec 2026-09-14: placed at phase 5 from the INPUT, not grown post-hoc.
    def test_emphasis_grows_a_missing_beta_row(self):
        from practical_asset_allocation.human_override import HumanOverridePreferences

        base = {r.subgroup: r.total for r in _run_practical().aggregated_subgroups}
        assert base.get("medium_beta_equities", 0.0) == 0.0, "fixture must start empty"
        out, _s4 = _run_practical_with_prefs_traced(
            HumanOverridePreferences(subgroup_emphasis={"medium_beta_equities": 40.0})
        )
        rows = {r.subgroup: r.total for r in out.aggregated_subgroups}
        assert rows.get("medium_beta_equities", 0.0) > 0

    # test_exclusion_zeroes_row_and_redistributes_within_class_per_column was
    # DELETED (spec 2026-09-14, Task C1): it asserted the per-column pro-rata
    # ALGORITHM of _redistribute_within_class — the step-6 reshape, removed
    # here. It has no successor: the engine honours an exclusion by never
    # PLACING the money (phase 5 for equity rows, D-B3 for gold), so there is
    # nothing to redistribute. Its subject, the short-term `arbitrage` row, is
    # one the engine does not yet read an exclusion for at all.

    # spec 2026-09-14: placed at phase 5 against the WHOLE portfolio (see the
    # basis note on test_emphasis_on_beta_subgroup_carries_market_cap_ask).
    def test_emphasis_lifts_named_row_within_class(self):
        from asset_allocation_pydantic.utils import round_to_100
        from practical_asset_allocation.human_override import HumanOverridePreferences

        out, s4 = _run_practical_with_prefs_traced(
            HumanOverridePreferences(subgroup_emphasis={"value_equities": 13.5})
        )
        val = next(
            r.total for r in out.aggregated_subgroups if r.subgroup == "value_equities"
        )
        # The pin is PLACED at phase 5 exactly; the slider then drops the dust
        # rows around it and hands their rupees to the one survivor, so the
        # final row sits at or above the ask (D-A5 — a pin is a floor).
        assert s4["initial_equity_subgroup_amounts"]["value_equities"] == round_to_100(
            out.grand_total * 0.135
        )
        assert val >= out.grand_total * 0.135 - 1000

    def test_emphasis_on_class_with_no_peers_does_not_destroy_money(self):
        """C2 repro: gold_commodities is the ONLY 'others' subgroup in
        CANONICAL_SUBGROUP_ORDER, so an emphasis on it has no same-class peer
        to shrink into/out of. Pre-fix, the shrink branch computed a freed
        amount with nowhere to put it and silently dropped it — reviewer
        measured ₹713,400 vanish from a ₹2Cr plan (rows summed to
        19,286,600 while output.grand_total still said 20,000,000)."""
        out = _run_practical()
        reshaped, _ = self._apply(out, subgroup_emphasis={"gold_commodities": 50})
        rows_sum = sum(r.total for r in reshaped.aggregated_subgroups)
        assert abs(rows_sum - out.grand_total) < 500, (
            f"reshape lost money: rows sum to {rows_sum}, grand_total is "
            f"{out.grand_total}"
        )

    # test_conservation_assertion_fires_on_a_hand_broken_reshape was DELETED
    # (spec 2026-09-14, Task C1): it monkeypatched _apply_exclusions to prove
    # _assert_conserved fires on a leaky reshape. With no reshape left in
    # apply_human_override there is nothing for that guard to guard, and the
    # rows it would have checked are the engine's own, conserved by
    # construction (pinned by test_uniform_subgroup_pins.py's §5 invariant).

    # spec 2026-09-14: over-subscription is reconciled by _fit_pins_to_room
    # BEFORE the sleeve is sized, and the disclosure signal moved with it —
    # it is the run's `pins_scaled` flag (D-A4) that apply_human_override now
    # reads, not a note the reshape produced.
    def test_oversubscribed_asks_scale_proportionally(self):
        # Three heavies = 120% of the equity class asked -> all three scale by
        # the same factor; nobody silently wins over the others.
        from practical_asset_allocation.human_override import HumanOverridePreferences

        out, s4 = _run_practical_with_prefs_traced(
            HumanOverridePreferences(subgroup_emphasis={
                "low_beta_equities": 40.0,
                "medium_beta_equities": 40.0,
                "high_beta_equities": 40.0,
            })
        )
        placed = s4["equity_subgroup_amounts"]
        betas = [
            placed[sg] for sg in
            ("low_beta_equities", "medium_beta_equities", "high_beta_equities")
        ]
        assert min(betas) > 0
        for amt in betas:
            assert abs(amt - betas[0]) <= 100, betas  # equal asks, equal scale
        assert sum(betas) <= s4["residual_equity_corpus_final"]
        assert s4["pins_scaled"] is True
        rows = {r.subgroup: r.total for r in out.aggregated_subgroups}
        assert abs(sum(rows.values()) - out.grand_total) < 500

    def test_multi_ask_is_order_independent(self):
        out = _run_practical()
        a, _ = self._apply(out, subgroup_emphasis={
            "low_beta_equities": 40.0, "medium_beta_equities": 40.0,
            "high_beta_equities": 40.0,
        })
        b, _ = self._apply(out, subgroup_emphasis={
            "high_beta_equities": 40.0, "medium_beta_equities": 40.0,
            "low_beta_equities": 40.0,
        })
        rows_a = {r.subgroup: round(r.total, 2) for r in a.aggregated_subgroups}
        rows_b = {r.subgroup: round(r.total, 2) for r in b.aggregated_subgroups}
        assert rows_a == rows_b

    # spec 2026-09-14: both asks are INPUT pins placed at phase 5, against
    # the WHOLE portfolio (see the basis note above).
    def test_fitting_multi_ask_honored_exactly(self):
        # 13.5 + 9 = 22.5% of the portfolio asked: both honored exactly; the
        # engine fills the rest of the class itself; nothing to disclose.
        from asset_allocation_pydantic.utils import round_to_100
        from practical_asset_allocation.human_override import HumanOverridePreferences

        out, s4 = _run_practical_with_prefs_traced(
            HumanOverridePreferences(subgroup_emphasis={
                "low_beta_equities": 13.5, "high_beta_equities": 9.0,
            })
        )
        tot = out.grand_total
        assert s4["equity_subgroup_amounts"]["low_beta_equities"] == round_to_100(tot * 0.135)
        assert s4["equity_subgroup_amounts"]["high_beta_equities"] == round_to_100(tot * 0.09)
        rows = {r.subgroup: r.total for r in out.aggregated_subgroups}
        assert abs(rows["low_beta_equities"] - tot * 0.135) < 500
        assert abs(rows["high_beta_equities"] - tot * 0.09) < 500
        assert abs(sum(rows.values()) - out.grand_total) < 500
        assert s4["pins_scaled"] is False
        assert out.human_override_applied.shortfall_reason is None

    # spec 2026-09-14 (D-B3): the exclusion arrives on the INPUT and the
    # commodity CLASS goes to zero before the sleeve is sized — the money
    # never reaches gold rather than being moved out of it afterwards.
    def test_zero_on_only_class_row_moves_money_cross_class(self):
        # "I don't want gold" when gold is the ONLY others row: the money
        # must still leave (hard exclusion semantics of a 0 entry) — it
        # relocates to other classes rather than silently staying put.
        from practical_asset_allocation.human_override import HumanOverridePreferences

        out = _run_practical()
        before = {r.subgroup: r.total for r in out.aggregated_subgroups}
        assert before.get("gold_commodities", 0.0) > 0
        excluded, _s4 = _run_practical_with_prefs_traced(
            HumanOverridePreferences(subgroup_emphasis={"gold_commodities": 0.0})
        )
        rows = {r.subgroup: r.total for r in excluded.aggregated_subgroups}
        assert rows.get("gold_commodities", 0.0) == 0.0
        grand_before = sum(before.values())
        grand_after = sum(rows.values())
        assert abs(grand_after - grand_before) < 500

    # Spec 2026-09-14: the class ask arrives on the engine INPUT (phase 2) and
    # the subgroup emphasis still composes on top in step 6 — no post-hoc call.
    def test_class_target_and_beta_emphasis_compose(self):
        from practical_asset_allocation.human_override import HumanOverridePreferences

        reshaped = _run_practical_with_prefs(
            HumanOverridePreferences(
                asset_class_requested={"equity": 80.0, "debt": 15.0, "others": 5.0},
                subgroup_emphasis={"high_beta_equities": 40.0},
            )
        )
        assert abs(_class_mix_pct(reshaped)["equity"] - 80.0) < 2.0
        rows = {r.subgroup: r.total for r in reshaped.aggregated_subgroups}
        # Spec 2026-09-14 (D-A3): a sub-group ask is a share of the WHOLE
        # PORTFOLIO, honoured at phase 5. It used to be a share of the
        # MIGRATABLE equity (class minus ELSS, direct stock and the sleeve)
        # because step 6 could only reshape what it was allowed to move. The
        # engine is now the more correct of the two, so the basis here moves
        # with it — 40% of the portfolio is half of the 80% equity class the
        # old %-of-class number named.
        assert abs(rows.get("high_beta_equities", 0.0) - reshaped.grand_total * 0.40) < 5000


class TestPipelineWiring:
    def test_input_accepts_human_override_and_output_carries_applied(self):
        from practical_asset_allocation.human_override import HumanOverridePreferences
        from practical_asset_allocation.pipeline import run_practical_allocation
        from test_human_override_golden import make_practical_input

        inp = make_practical_input()
        inp = inp.model_copy(update={
            "human_override": HumanOverridePreferences(
                asset_class_requested={"equity": 80.0, "debt": 15.0, "others": 5.0}
            )
        })
        out = run_practical_allocation(inp)
        assert out.human_override_applied is not None
        # Honored in CARVED basis (what the customer sees): 80% ask -> 80%
        # of the carved breakdown.
        assert abs(
            out.asset_class_breakdown.recommended.equity_total_pct - 80.0
        ) < 2.0

    def test_no_override_output_has_none_applied(self):
        from practical_asset_allocation.pipeline import run_practical_allocation
        from test_human_override_golden import make_practical_input

        out = run_practical_allocation(make_practical_input())
        assert out.human_override_applied is None


class TestClassTargetCarvedBasis:
    """C1 fix: the class target is honored in CARVED basis — the numbers the
    customer sees (multi_asset split into its equity/debt/others parts), NOT
    row basis (multi_asset counted wholly as equity). requested and achieved
    must therefore land in the SAME basis."""

    # Spec 2026-09-14: the resolved class ask arrives on the engine INPUT
    # (phase 2 of the long-term step), not via a post-hoc step-6 call.
    def _apply_more(self, cls):
        from test_human_override_golden import make_practical_input
        from practical_asset_allocation.pipeline import run_practical_allocation
        from practical_asset_allocation.human_override import HumanOverridePreferences
        from app.domains.mutual_funds.services.investment_preferences import (
            resolve_saved_preferences,
        )

        inp = make_practical_input()
        out = run_practical_allocation(inp)
        b = out.asset_class_breakdown.recommended
        base = {"equity": b.equity_total_pct, "debt": b.debt_total_pct,
                "others": b.others_total_pct}
        req = resolve_saved_preferences(
            {"asset_class": {"class": cls, "direction": "more"}},
            current_class_mix_pct=base, current_subgroup_share_pct={},
        ).asset_class_requested
        preferred = run_practical_allocation(
            inp.model_copy(
                update={
                    "human_override": HumanOverridePreferences(
                        asset_class_requested=req
                    )
                }
            )
        )
        return base, req, _class_mix_pct(preferred)

    # Spec 2026-09-14: preference applied on the engine input via
    # _apply_more, not through a post-hoc step-6 call.
    def test_more_equity_delivers_carved_plus_ten(self):
        base, req, achieved = self._apply_more("equity")
        # "more equity" = carved baseline + 10; achieved must land there.
        assert abs(achieved["equity"] - (base["equity"] + 10.0)) < 1.0
        # requested and achieved in the SAME basis.
        assert abs(achieved["equity"] - req["equity"]) < 1.0

    # Spec 2026-09-14: preference applied on the engine input via
    # _apply_more, not through a post-hoc step-6 call.
    def test_more_debt_delivers_carved_plus_ten_not_overshoot(self):
        base, req, achieved = self._apply_more("debt")
        assert abs(achieved["debt"] - (base["debt"] + 10.0)) < 1.0
        assert abs(achieved["debt"] - req["debt"]) < 1.0

    # Spec 2026-09-14: preference applied on the engine input via
    # _apply_more, not through a post-hoc step-6 call.
    def test_all_three_classes_land_in_requested_basis(self):
        _, req, achieved = self._apply_more("equity")
        for c in ("equity", "debt", "others"):
            assert abs(achieved[c] - req[c]) < 1.5, (c, achieved[c], req[c])


class TestDisclosure:
    """I1-I3: money-visible reshapes that used to be silent must disclose."""

    def _run_with_comp(self, **prefs_kwargs):
        from test_human_override_golden import make_practical_input
        from practical_asset_allocation.pipeline import run_practical_allocation
        from practical_asset_allocation.human_override import (
            HumanOverridePreferences, apply_human_override,
        )
        inp = make_practical_input()
        out = run_practical_allocation(inp)
        return apply_human_override(
            out, HumanOverridePreferences(**prefs_kwargs), inp.multi_asset_composition
        )

    # spec 2026-09-14: both facets ride in on the engine INPUT; step 6 reads
    # the finished mix and discloses the gap.
    def test_i2_exclusion_annulling_class_target_is_disclosed(self):
        # others 20% requested, but gold (the sole others row) excluded ->
        # others can't be honored; must be disclosed, not silent.
        from practical_asset_allocation.human_override import HumanOverridePreferences

        out = _run_practical_with_prefs(
            HumanOverridePreferences(
                asset_class_requested={"equity": 60.0, "debt": 20.0, "others": 20.0},
                subgroup_emphasis={"gold_commodities": 0.0},
            )
        )
        applied = out.human_override_applied
        assert _class_mix_pct(out)["others"] < 5.0
        assert applied.shortfall_reason and "others" in applied.shortfall_reason.lower()

    # test_i3_emphasis_on_dead_class_is_disclosed was DELETED (spec
    # 2026-09-14): it called _apply_emphasis directly — step 6's reshape,
    # retired in Part C. The disclosure it checked now comes from the
    # engine's own path; nothing in production called that function.

    def test_i1_emergency_buffer_cut_is_disclosed(self):
        # a heavy equity ask shrinks debt/others uniformly incl. emergency;
        # the reduction to the safety buffer must be disclosed.
        _, applied = self._run_with_comp(
            asset_class_requested={"equity": 85.0, "debt": 10.0, "others": 5.0},
        )
        assert applied.shortfall_reason and "emergency" in applied.shortfall_reason.lower()


class TestSubgroupAsksPreserveClassMix:
    """Finding #2 fix: a within-class subgroup ask (emphasis or exclusion)
    must NOT move the carved CLASS mix — multi_asset is a fixed contributor
    there too (consistent with ruling 10), not an adjustable peer."""

    def _neutral(self):
        from test_human_override_golden import make_practical_input
        from practical_asset_allocation.pipeline import run_practical_allocation
        inp = make_practical_input()
        return run_practical_allocation(inp), inp.multi_asset_composition

    def _eq(self, out):
        return out.asset_class_breakdown.recommended.equity_total_pct

    def test_beta_emphasis_does_not_move_equity_class(self):
        from practical_asset_allocation.human_override import (
            HumanOverridePreferences, apply_human_override,
        )
        out, comp = self._neutral()
        before = self._eq(out)
        reshaped, _ = apply_human_override(
            out, HumanOverridePreferences(subgroup_emphasis={"high_beta_equities": 40.0}),
            comp,
        )
        assert abs(self._eq(reshaped) - before) < 0.5, (self._eq(reshaped), before)

    def test_exclusion_does_not_move_equity_class(self):
        from practical_asset_allocation.human_override import (
            HumanOverridePreferences, apply_human_override,
        )
        out, comp = self._neutral()
        before = self._eq(out)
        reshaped, _ = apply_human_override(
            out, HumanOverridePreferences(subgroup_emphasis={"us_equities": 0.0}),
            comp,
        )
        assert abs(self._eq(reshaped) - before) < 0.5, (self._eq(reshaped), before)


# TestMultiAssetExclusionSplitsByComposition was DELETED (spec 2026-09-14,
# Task C1): it asserted that step 6's reshape emptied an excluded multi_asset
# row by splitting it 65/25/10 into its class peers. The reshape is gone, and
# the composition split it exercised has no successor — the engine does not
# read an exclusion of `multi_asset` at all yet (`_is_migratable` had blocked
# the sleeve from ever moving). Plan Task D1 unblocks multi_asset as a settable
# sub-group end to end; that is where this behaviour gets a real home.
