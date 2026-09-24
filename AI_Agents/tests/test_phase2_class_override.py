"""Phase-2 class-split injection (spec 2026-09-14 §4.1): the customer's
equity / debt / commodity preference is the phase-2 output itself, not a
post-hoc step-6 reshape. Practical engine only — the ideal engine is
Prozpr's preference-free recommendation."""

from __future__ import annotations

import sys
from pathlib import Path

# AI_Agents/tests is a package (__init__.py), so sibling test modules are not
# importable bare under pytest — same shim as test_human_override_step.py.
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from test_human_override_golden import (  # noqa: E402
    make_practical_input,
    trim_disclosure,
)


# ── Task 1: the shared phase-2 function ──────────────────────────────────────


def _gated_bounds():
    """Bounds as the others-gate leaves them for an aggressive profile:
    commodity hard-zeroed (others_max = 0). On the engine path this clamps
    commodity to 0 — the customer's number must survive it."""
    from asset_allocation_pydantic.steps.step4_long_term import ResolvedBounds

    return ResolvedBounds(
        eq_min=65, eq_max=108, debt_min=0, debt_max=27, others_min=0, others_max=0
    )


class TestPhase2RequestedSplit:
    def test_requested_split_is_the_phase2_output(self):
        from asset_allocation_pydantic.models import MarketCommentaryScores
        from asset_allocation_pydantic.steps.step4_long_term import (
            phase2_asset_class_pcts,
        )

        out = phase2_asset_class_pcts(
            _gated_bounds(),
            MarketCommentaryScores(),
            requested_class_pcts={"equity": 85.0, "debt": 7.0, "others": 8.0},
        )
        assert out == (85, 7, 8)

    def test_requested_split_bypasses_the_others_gate(self):
        # others_max = 0 would clamp commodity to 0 on the engine path (D3).
        from asset_allocation_pydantic.models import MarketCommentaryScores
        from asset_allocation_pydantic.steps.step4_long_term import (
            phase2_asset_class_pcts,
        )

        _, _, others = phase2_asset_class_pcts(
            _gated_bounds(),
            MarketCommentaryScores(),
            requested_class_pcts={"equity": 85.0, "debt": 7.0, "others": 8.0},
        )
        assert others == 8

    def test_requested_split_rounds_to_int_pcts_summing_to_100(self):
        from asset_allocation_pydantic.models import MarketCommentaryScores
        from asset_allocation_pydantic.steps.step4_long_term import (
            phase2_asset_class_pcts,
        )

        out = phase2_asset_class_pcts(
            _gated_bounds(),
            MarketCommentaryScores(),
            requested_class_pcts={"equity": 33.3, "debt": 33.3, "others": 33.4},
        )
        assert sum(out) == 100
        assert all(isinstance(v, int) for v in out)


# ── Task 2: overall-portfolio preference → long-term split ───────────────────


class TestOverallToLongTerm:
    def test_nothing_committed_is_the_identity(self):
        from practical_asset_allocation.pipeline import _lt_class_targets_from_overall

        pcts = _lt_class_targets_from_overall(
            {"equity": 85.0, "debt": 7.0, "others": 8.0},
            total_corpus=100.0,
            committed={"equity": 0.0, "debt": 0.0, "others": 0.0},
        )
        assert pcts == {"equity": 85.0, "debt": 7.0, "others": 8.0}
    def test_buffer_debt_is_subtracted_so_the_overall_lands_on_target(self):
        # Emergency buffer already holds 5 of debt. Ask 85/7/8 overall →
        # long-term must carry only 2 of debt, over a 95 LT corpus.
        from practical_asset_allocation.pipeline import _lt_class_targets_from_overall

        pcts = _lt_class_targets_from_overall(
            {"equity": 85.0, "debt": 7.0, "others": 8.0},
            total_corpus=100.0,
            committed={"equity": 0.0, "debt": 5.0, "others": 0.0},
        )
        lt_corpus = 95.0
        overall_debt = 5.0 + pcts["debt"] * lt_corpus / 100.0
        overall_eq = pcts["equity"] * lt_corpus / 100.0
        overall_ot = pcts["others"] * lt_corpus / 100.0
        assert abs(overall_eq - 85.0) < 1e-6
        assert abs(overall_debt - 7.0) < 1e-6
        assert abs(overall_ot - 8.0) < 1e-6
        assert abs(sum(pcts.values()) - 100.0) < 1e-6
    def test_ask_below_the_buffer_floors_at_zero_and_keeps_the_customer_ratio(self):
        # Buffer holds 5 of debt but the customer asks for 3 overall: LT gets
        # 0 debt and the rest scale proportionally. (The disclosure that
        # "what's already committed wins" is derived from the FINAL numbers
        # in step 6 — nothing to flag here.)
        from practical_asset_allocation.pipeline import _lt_class_targets_from_overall

        pcts = _lt_class_targets_from_overall(
            {"equity": 90.0, "debt": 3.0, "others": 7.0},
            total_corpus=100.0,
            committed={"equity": 0.0, "debt": 5.0, "others": 0.0},
        )
        assert pcts["debt"] == 0.0
        assert abs(sum(pcts.values()) - 100.0) < 1e-6
        # equity : others keep the customer's 90 : 7 ratio
        assert abs(pcts["equity"] / pcts["others"] - 90.0 / 7.0) < 1e-6

    def test_committed_by_class_rolls_step_outputs_up_via_the_class_table(self):
        from types import SimpleNamespace

        from practical_asset_allocation.pipeline import _committed_by_class

        s1 = SimpleNamespace(subgroup_amounts={"short_debt": 300_000})
        s2 = SimpleNamespace(subgroup_amounts={"arbitrage": 200_000})
        s3 = SimpleNamespace(subgroup_amounts={"arbitrage_plus_income": 100_000})
        out = _committed_by_class(s1, s2, s3)
        assert out["debt"] == 600_000.0
        assert out["equity"] == 0.0
        assert out["others"] == 0.0

    def test_committed_accounts_for_everything_steps_1_2_consumed(self):
        """Guard the conversion's premise: ``subgroup_amounts`` must capture
        EVERY rupee steps 1-2 removed from the corpus. If a future carve lands
        outside it, the overall->LT split would silently skew — fail here."""
        from practical_asset_allocation.pipeline import run_practical_allocation

        inp = make_practical_input(monthly_household_expense=500_000)
        trace: dict = {}
        run_practical_allocation(inp, trace=trace)
        committed = sum(
            sum(trace[k]["subgroup_amounts"].values())
            for k in ("step1_emergency", "step2_short_term")
        )
        consumed = trace["rebalancing_corpus"] - trace["lt_corpus_entering"]
        assert abs(committed - consumed) < 1.0


# ── Task 2b: input guard ─────────────────────────────────────────────────────


class TestClassPreferenceRangeGuard:
    def test_negative_class_pct_is_rejected(self):
        import pytest

        from practical_asset_allocation.human_override import HumanOverridePreferences

        # Sums to 100, so the pre-existing sum check passes — the RANGE check
        # is what must catch it (these values would become rupee amounts).
        with pytest.raises(ValueError):
            HumanOverridePreferences(
                asset_class_requested={"equity": 110.0, "debt": -10.0, "others": 0.0}
            )

    def test_valid_mix_is_still_accepted(self):
        from practical_asset_allocation.human_override import HumanOverridePreferences

        p = HumanOverridePreferences(
            asset_class_requested={"equity": 85.0, "debt": 7.0, "others": 8.0}
        )
        assert p.is_empty() is False


# ── Task 3: end-to-end through the practical engine ──────────────────────────


def _recommended(out):
    r = out.asset_class_breakdown.recommended
    return r.equity_total_pct, r.debt_total_pct, r.others_total_pct


def _with_prefs(inp, **class_pcts):
    from practical_asset_allocation.human_override import HumanOverridePreferences

    return inp.model_copy(
        update={
            "human_override": HumanOverridePreferences(
                asset_class_requested=dict(class_pcts)
            )
        }
    )


class TestPracticalEngineHonoursTheClassPreference:
    def test_85_7_8_lands_exactly_including_debt(self):
        """THE fix. Debt reaches 7 because the sleeve now sizes itself to the
        7% it is fed — the old step-6 reshape pinned it at the engine's debt.
        The default fixture carries a real emergency buffer, so this also
        proves the overall-portfolio conversion (Task 2) end to end."""
        from practical_asset_allocation.pipeline import run_practical_allocation

        out = run_practical_allocation(
            _with_prefs(make_practical_input(), equity=85.0, debt=7.0, others=8.0)
        )
        eq, dt, ot = _recommended(out)
        assert abs(eq - 85.0) < 1.5
        assert abs(dt - 7.0) < 1.5  # the number the old engine could not reach
        assert abs(ot - 8.0) < 1.5
        applied = out.human_override_applied
        assert applied is not None
        assert trim_disclosure(applied) is None

    def test_class_preference_lands_despite_the_others_gate_on_an_aggressive_profile(self):
        # Risk 9.5 + a tepid commodity view fires BOTH others-gates. On the
        # engine path that clamps commodity at phase 2 (the sleeve then
        # manufactures ~5% of it out of equity). With a preference the
        # requested split IS the phase-2 output — gates bypassed (D3) — so all
        # three classes land, not commodity at equity's expense (the old
        # step-6 reshape gave ~78/14/8 here).
        from asset_allocation_pydantic.models import MarketCommentaryScores
        from practical_asset_allocation.pipeline import run_practical_allocation

        out = run_practical_allocation(
            _with_prefs(
                make_practical_input(
                    effective_risk_score=9.5,
                    market_commentary=MarketCommentaryScores(others=1.0),
                ),
                equity=85.0, debt=7.0, others=8.0,
            )
        )
        eq, dt, ot = _recommended(out)
        assert abs(eq - 85.0) < 1.5
        assert abs(dt - 7.0) < 1.5
        assert abs(ot - 8.0) < 1.5

    def test_locked_elss_still_floors_equity_and_discloses(self):
        # 12 of 20 in locked ELSS; the customer asks for only 40% equity.
        # Reality wins: equity is lifted to the locked floor and disclosed.
        from practical_asset_allocation.pipeline import run_practical_allocation

        out = run_practical_allocation(
            _with_prefs(
                make_practical_input(elss_corpus=12_000_000.0, mf_corpus=7_000_000.0),
                equity=40.0, debt=50.0, others=10.0,
            )
        )
        applied = out.human_override_applied
        assert applied is not None
        # Task C2: achieved is the run's own mix; the ask is the 40% above.
        assert _recommended(out)[0] > 40.0 + 5.0
        assert applied.shortfall_reason is not None
        assert "already committed" in applied.shortfall_reason

    def test_debt_ask_below_the_emergency_buffer_is_now_honoured(self):
        # AMENDED by spec 2026-09-15 §3. A large household expense builds a
        # large emergency buffer (debt), and this ask is smaller than it. That
        # used to be unhonourable — what steps 1-3 had committed won, and the
        # shortfall was disclosed. Setting a preference now SUSPENDS those
        # carve-outs, so nothing is committed and the 3% lands.
        from practical_asset_allocation.pipeline import run_practical_allocation

        base = make_practical_input(monthly_household_expense=500_000)
        # Guard the fixture: without a preference the buffer must still be
        # bigger than the ask, or this test proves nothing about suspension.
        neutral = run_practical_allocation(base)
        buffer_pct = (
            100.0
            * sum(r.emergency for r in neutral.aggregated_subgroups)
            / float(base.total_corpus)
        )
        assert buffer_pct > 3.0 + 1.0, "fixture must build a buffer larger than the 3% debt ask"

        out = run_practical_allocation(
            _with_prefs(base, equity=90.0, debt=3.0, others=7.0)
        )
        applied = out.human_override_applied
        assert applied is not None
        assert abs(_recommended(out)[1] - 3.0) < 1.5, "the debt ask now lands"
        assert sum(r.emergency for r in out.aggregated_subgroups) == 0
