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

    def test_equity_up_hits_target_and_conserves_grand_total(self):
        from practical_asset_allocation.human_override import (
            HumanOverridePreferences,
            apply_human_override,
        )

        out = _run_practical()
        prefs = HumanOverridePreferences(
            asset_class_requested={"equity": 80.0, "debt": 15.0, "others": 5.0}
        )
        reshaped, applied = apply_human_override(out, prefs, None)
        mix = _class_mix_pct(reshaped)
        assert abs(mix["equity"] - 80.0) < 1.5
        assert abs(sum(r.total for r in reshaped.aggregated_subgroups)
                   - sum(r.total for r in out.aggregated_subgroups)) < 500
        assert applied is not None and applied.requested["equity"] == 80.0
        assert abs(applied.achieved["equity"] - mix["equity"]) < 0.01
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

    def test_shrink_is_uniform_growth_lands_in_long_term(self):
        from practical_asset_allocation.human_override import (
            HumanOverridePreferences,
            apply_human_override,
        )

        out = _run_practical()
        prefs = HumanOverridePreferences(
            asset_class_requested={"equity": 80.0, "debt": 15.0, "others": 5.0}
        )
        reshaped, _ = apply_human_override(out, prefs, None)
        from practical_asset_allocation.human_override import CLASS_OF, FROZEN_SUBGROUPS

        before = {r.subgroup: r for r in out.aggregated_subgroups}
        after = {r.subgroup: r for r in reshaped.aggregated_subgroups}
        # Shrinking debt rows keep bucket proportions (uniform factor).
        for sg in ("short_debt", "arbitrage", "arbitrage_plus_income"):
            if sg in before and before[sg].total > 0 and after[sg].total > 0:
                f = after[sg].total / before[sg].total
                assert f < 1.0
                for col in ("emergency", "short_term", "medium_term", "long_term"):
                    assert abs(getattr(after[sg], col)
                               - getattr(before[sg], col) * f) < 200
        # Growing equity rows grew ONLY in long_term.
        for sg, row in after.items():
            if sg in FROZEN_SUBGROUPS or sg not in before:
                continue
            if CLASS_OF.get(sg) == "equity" and row.total > before[sg].total:
                for col in ("emergency", "short_term", "medium_term"):
                    assert abs(getattr(row, col) - getattr(before[sg], col)) < 200

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
        assert applied.achieved["equity"] > 50.0  # frozen floor kept it high
        assert applied.shortfall_reason is not None

    def test_apply_class_targets_zero_grand_total_returns_tuple(self):
        from asset_allocation_pydantic.models import AggregatedSubgroupRow
        from practical_asset_allocation.human_override import _apply_class_targets

        rows = [
            AggregatedSubgroupRow(
                subgroup="short_debt", emergency=0.0, short_term=0.0,
                medium_term=0.0, long_term=0.0, total=0.0,
            ),
        ]
        result = _apply_class_targets(
            rows, {"equity": 60.0, "debt": 30.0, "others": 10.0}
        )
        out_rows, floors_bound = result
        assert out_rows == rows
        assert floors_bound is False


class TestSubgroupStages:
    def _apply(self, out, **pref_kwargs):
        from practical_asset_allocation.human_override import (
            HumanOverridePreferences,
            apply_human_override,
        )

        return apply_human_override(
            out, HumanOverridePreferences(**pref_kwargs), None
        )

    def test_emphasis_on_beta_subgroup_carries_market_cap_ask(self):
        # "smallcap heavy" resolves app-side to emphasis on high_beta_equities
        # (share of the WHOLE equity class, not just the beta sleeve).
        from practical_asset_allocation.human_override import CLASS_OF, FROZEN_SUBGROUPS

        out = _run_practical()

        def migratable_equity(subgroup_rows):
            # The engine's own class base: non-frozen rows whose CLASS_OF is
            # equity (multi_asset counts wholly as equity here).
            return sum(
                r.total for r in subgroup_rows
                if CLASS_OF.get(r.subgroup, "others") == "equity"
                and r.subgroup not in FROZEN_SUBGROUPS
                and r.subgroup != "multi_asset"
            )

        eq_before = migratable_equity(out.aggregated_subgroups)
        reshaped, _ = self._apply(
            out, subgroup_emphasis={"high_beta_equities": 50.0}
        )
        rows = {r.subgroup: r.total for r in reshaped.aggregated_subgroups}
        eq_after = migratable_equity(reshaped.aggregated_subgroups)
        assert abs(eq_after - eq_before) < 500
        assert abs(rows.get("high_beta_equities", 0.0) - eq_before * 0.5) < 500

    def test_emphasis_grows_a_missing_beta_row(self):
        out = _run_practical()
        reshaped, _ = self._apply(
            out, subgroup_emphasis={"medium_beta_equities": 40.0}
        )
        rows = {r.subgroup: r.total for r in reshaped.aggregated_subgroups}
        assert rows.get("medium_beta_equities", 0.0) > 0

    def test_exclusion_zeroes_row_and_redistributes_within_class_per_column(self):
        # Default fixture (no goals) never routes to "arbitrage" (step2 needs
        # both tax_rate > 20% and a short-term goal — see step2_short_term.py)
        # — add both so the row this test excludes actually holds money.
        from asset_allocation_pydantic.models import Goal

        out = _run_practical(
            effective_tax_rate=25.0,
            goals=[Goal(
                goal_name="short_term_goal", time_to_goal_months=12,
                amount_needed=500_000.0, goal_priority="negotiable",
            )],
        )
        before = {r.subgroup: r for r in out.aggregated_subgroups}
        assert before["arbitrage"].total > 0, "fixture must allocate arbitrage"
        reshaped, _ = self._apply(out, subgroup_emphasis={"arbitrage": 0.0})
        after = {r.subgroup: r for r in reshaped.aggregated_subgroups}
        assert after["arbitrage"].total == 0.0
        # Debt class total preserved.
        from practical_asset_allocation.human_override import CLASS_OF
        debt_before = sum(
            r.total for r in out.aggregated_subgroups
            if CLASS_OF.get(r.subgroup) == "debt"
        )
        debt_after = sum(
            r.total for r in reshaped.aggregated_subgroups
            if CLASS_OF.get(r.subgroup) == "debt"
        )
        assert abs(debt_before - debt_after) < 500
        # Column-wise, genuinely preserved: no OTHER debt row carries a
        # short_term balance in this engine (step2 routes the whole
        # short-term bucket to a single winner subgroup), so the per-column
        # pro-rata-by-column split has no target within the column — the
        # last-resort fallback then splits the freed amount pro-rata by ROW
        # TOTAL across the same-class survivors, but keeps it in the SAME
        # (short_term) column. It must never get relabeled into long_term.
        st_before = sum(r.short_term for r in out.aggregated_subgroups)
        st_after = sum(r.short_term for r in reshaped.aggregated_subgroups)
        assert abs(st_before - st_after) < 1.0

        debt_survivor_total = (
            before["short_debt"].total + before["arbitrage_plus_income"].total
        )
        expected_short_debt_gain = (
            500_000.0 * before["short_debt"].total / debt_survivor_total
        )
        expected_api_gain = (
            500_000.0 * before["arbitrage_plus_income"].total / debt_survivor_total
        )
        assert (
            after["short_debt"].short_term - before["short_debt"].short_term
        ) == pytest.approx(expected_short_debt_gain, abs=1.0)
        assert (
            after["arbitrage_plus_income"].short_term
            - before["arbitrage_plus_income"].short_term
        ) == pytest.approx(expected_api_gain, abs=1.0)
        # long_term untouched — the freed money was never relabeled.
        assert (
            after["arbitrage_plus_income"].long_term
            == before["arbitrage_plus_income"].long_term
        )

    def test_emphasis_lifts_named_row_within_class(self):
        out = _run_practical()
        reshaped, _ = self._apply(out, subgroup_emphasis={"value_equities": 30.0})
        from practical_asset_allocation.human_override import CLASS_OF
        eq_total = sum(
            r.total for r in reshaped.aggregated_subgroups
            if CLASS_OF.get(r.subgroup) == "equity"
            and r.subgroup not in ("tax_efficient_equities", "non_mf_equities")
            and r.subgroup != "multi_asset"
        )
        val = next(
            r.total for r in reshaped.aggregated_subgroups
            if r.subgroup == "value_equities"
        )
        assert abs(val - eq_total * 0.30) < 1000

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

    def test_conservation_assertion_fires_on_a_hand_broken_reshape(self, monkeypatch):
        """Spec §3.2.7: apply_human_override must fail loud (ValueError) if a
        reshape step ever loses money, rather than silently returning an
        internally-inconsistent output. Prove the guard is actually live by
        forcing a step to drop money and asserting it fires."""
        import practical_asset_allocation.human_override as ho

        def _broken_apply_exclusions(rows, exclusions, multi_asset_composition=None):
            # Zero the excluded row's total without moving the freed amount
            # anywhere — exactly the kind of silent leak the guard exists for.
            out = []
            for r in rows:
                if r.subgroup in exclusions and r.total > 0:
                    out.append(r.model_copy(update={"total": 0.0}))
                else:
                    out.append(r)
            return out

        monkeypatch.setattr(ho, "_apply_exclusions", _broken_apply_exclusions)
        out = _run_practical()
        assert any(
            r.subgroup == "us_equities" and r.total > 0
            for r in out.aggregated_subgroups
        ), "fixture must hold us_equities for the broken exclusion to lose money"
        prefs = ho.HumanOverridePreferences(subgroup_emphasis={"us_equities": 0.0})
        with pytest.raises(ValueError):
            ho.apply_human_override(out, prefs, None)

    def test_oversubscribed_asks_scale_proportionally(self):
        # Three heavies = 120% of equity asked -> each scaled by 100/120 to
        # ~33.3% of the class; nobody silently wins over the others.
        from practical_asset_allocation.human_override import CLASS_OF, FROZEN_SUBGROUPS

        out = _run_practical()
        reshaped, applied = self._apply(out, subgroup_emphasis={
            "low_beta_equities": 40.0,
            "medium_beta_equities": 40.0,
            "high_beta_equities": 40.0,
        })
        rows = {r.subgroup: r.total for r in reshaped.aggregated_subgroups}
        base = sum(
            r.total for r in reshaped.aggregated_subgroups
            if CLASS_OF.get(r.subgroup, "others") == "equity"
            and r.subgroup not in FROZEN_SUBGROUPS
            and r.subgroup != "multi_asset"
        )
        for sg in ("low_beta_equities", "medium_beta_equities", "high_beta_equities"):
            share = rows.get(sg, 0.0) * 100.0 / base
            assert abs(share - 100.0 / 3.0) < 0.5, (sg, share)
        assert applied.shortfall_reason and "scaled" in applied.shortfall_reason

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

    def test_fitting_multi_ask_honored_exactly(self):
        # 30 + 20 = 50% asked: both honored exactly; unconstrained equity
        # categories share the other 50% pro-rata; class total preserved.
        from practical_asset_allocation.human_override import CLASS_OF, FROZEN_SUBGROUPS

        out = _run_practical()
        eq_before = sum(
            r.total for r in out.aggregated_subgroups
            if CLASS_OF.get(r.subgroup, "others") == "equity"
            and r.subgroup not in FROZEN_SUBGROUPS
            and r.subgroup != "multi_asset"
        )
        reshaped, applied = self._apply(out, subgroup_emphasis={
            "low_beta_equities": 30.0, "high_beta_equities": 20.0,
        })
        rows = {r.subgroup: r.total for r in reshaped.aggregated_subgroups}
        eq_after = sum(
            r.total for r in reshaped.aggregated_subgroups
            if CLASS_OF.get(r.subgroup, "others") == "equity"
            and r.subgroup not in FROZEN_SUBGROUPS
            and r.subgroup != "multi_asset"
        )
        assert abs(eq_after - eq_before) < 500
        assert abs(rows["low_beta_equities"] - eq_before * 0.30) < 500
        assert abs(rows["high_beta_equities"] - eq_before * 0.20) < 500
        assert applied.shortfall_reason is None

    def test_zero_on_only_class_row_moves_money_cross_class(self):
        # "I don't want gold" when gold is the ONLY others row: the money
        # must still leave (hard exclusion semantics of a 0 entry) — it
        # relocates to other classes rather than silently staying put.
        out = _run_practical()
        before = {r.subgroup: r.total for r in out.aggregated_subgroups}
        assert before.get("gold_commodities", 0.0) > 0
        reshaped, _ = self._apply(out, subgroup_emphasis={"gold_commodities": 0.0})
        rows = {r.subgroup: r.total for r in reshaped.aggregated_subgroups}
        assert rows.get("gold_commodities", 0.0) == 0.0
        grand_before = sum(before.values())
        grand_after = sum(rows.values())
        assert abs(grand_after - grand_before) < 500

    def test_class_target_and_beta_emphasis_compose(self):
        out = _run_practical()
        reshaped, applied = self._apply(
            out,
            asset_class_requested={"equity": 80.0, "debt": 15.0, "others": 5.0},
            subgroup_emphasis={"high_beta_equities": 50.0},
        )
        from practical_asset_allocation.human_override import CLASS_OF, FROZEN_SUBGROUPS

        assert abs(applied.achieved["equity"] - 80.0) < 2.0
        rows = {r.subgroup: r.total for r in reshaped.aggregated_subgroups}
        migratable_eq = sum(
            r.total for r in reshaped.aggregated_subgroups
            if CLASS_OF.get(r.subgroup, "others") == "equity"
            and r.subgroup not in FROZEN_SUBGROUPS
            and r.subgroup != "multi_asset"
        )
        assert abs(rows.get("high_beta_equities", 0.0) - migratable_eq * 0.5) < 1000


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

    def _apply_more(self, cls):
        from test_human_override_golden import make_practical_input
        from practical_asset_allocation.pipeline import run_practical_allocation
        from practical_asset_allocation.human_override import (
            HumanOverridePreferences, apply_human_override,
        )
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
        _, applied = apply_human_override(
            out, HumanOverridePreferences(asset_class_requested=req),
            inp.multi_asset_composition,
        )
        return base, req, applied

    def test_more_equity_delivers_carved_plus_ten(self):
        base, req, applied = self._apply_more("equity")
        # "more equity" = carved baseline + 10; achieved must land there.
        assert abs(applied.achieved["equity"] - (base["equity"] + 10.0)) < 1.0
        # requested and achieved in the SAME basis.
        assert abs(applied.achieved["equity"] - req["equity"]) < 1.0

    def test_more_debt_delivers_carved_plus_ten_not_overshoot(self):
        base, req, applied = self._apply_more("debt")
        assert abs(applied.achieved["debt"] - (base["debt"] + 10.0)) < 1.0
        assert abs(applied.achieved["debt"] - req["debt"]) < 1.0

    def test_all_three_classes_land_in_requested_basis(self):
        _, req, applied = self._apply_more("equity")
        for c in ("equity", "debt", "others"):
            assert abs(applied.achieved[c] - req[c]) < 1.5, (c, applied.achieved[c], req[c])


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

    def test_i2_exclusion_annulling_class_target_is_disclosed(self):
        # others 20% requested, but gold (the sole others row) excluded ->
        # others can't be honored; must be disclosed, not silent.
        _, applied = self._run_with_comp(
            asset_class_requested={"equity": 60.0, "debt": 20.0, "others": 20.0},
            subgroup_emphasis={"gold_commodities": 0.0},
        )
        assert applied.achieved["others"] < 5.0
        assert applied.shortfall_reason and "others" in applied.shortfall_reason.lower()

    def test_i3_emphasis_on_dead_class_is_disclosed(self):
        from practical_asset_allocation.human_override import (
            AggregatedSubgroupRow, _apply_emphasis,
        )
        # only debt rows exist; emphasise an equity subgroup -> nowhere to apply
        rows = [AggregatedSubgroupRow(subgroup="short_debt", emergency=0.0,
                short_term=0.0, medium_term=0.0, long_term=1_000_000.0,
                total=1_000_000.0)]
        _, notes = _apply_emphasis(rows, {"high_beta_equities": 40.0})
        assert any("equity" in n for n in notes), notes

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


class TestMultiAssetExclusionSplitsByComposition:
    """Excluding a multi-asset fund must send its money to equity/debt/gold
    peers in its own 65/25/10 proportions — not dump the whole balance into
    equity — so the carved class mix the customer set stays put."""

    def test_class_mix_unchanged_after_excluding_multi_asset(self):
        from test_human_override_golden import make_practical_input
        from practical_asset_allocation.pipeline import run_practical_allocation
        from practical_asset_allocation.human_override import (
            HumanOverridePreferences, apply_human_override,
        )
        inp = make_practical_input()
        out = run_practical_allocation(inp)
        b0 = out.asset_class_breakdown.recommended
        assert any(r.subgroup == "multi_asset" and r.total > 0 for r in out.aggregated_subgroups)
        reshaped, _ = apply_human_override(
            out, HumanOverridePreferences(subgroup_emphasis={"multi_asset": 0.0}),
            inp.multi_asset_composition,
        )
        b1 = reshaped.asset_class_breakdown.recommended
        assert not any(r.subgroup == "multi_asset" and r.total > 0 for r in reshaped.aggregated_subgroups)
        for a, c in ((b0.equity_total_pct, b1.equity_total_pct),
                     (b0.debt_total_pct, b1.debt_total_pct),
                     (b0.others_total_pct, b1.others_total_pct)):
            assert abs(a - c) < 0.5, (a, c)
