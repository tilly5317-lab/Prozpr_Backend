"""D-A3 — the preference basis is % of the TOTAL portfolio, end to end.

The screen already spoke % of total and the engine spoke % of the sub-group's
own class, so every save divided by the class share and every read multiplied
it back. Both conversions are gone: storage, the resolvers and the engine all
speak one number now, and a pin the customer types is the pin the engine
places. Deleting the round-trip also deletes the pin-free probe run the
orchestrator needed purely to read the class amounts off (spec §7, D-A3).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from test_human_override_golden import make_practical_input  # noqa: E402

CLASS_MIX = {"equity": 72.0, "debt": 18.0, "others": 10.0}


class TestStorageSpeaksPctOfTotal:
    """The screen's number is stored verbatim — no basis change at the door."""

    def test_a_pin_passes_straight_through_on_save(self):
        from app.domains.profile.services.screen_preference_service import (
            resolve_screen_preferences,
        )

        r = resolve_screen_preferences(
            CLASS_MIX, [{"subgroup": "low_beta_equities", "pct_of_total": 40}]
        )
        # Old basis would have stored 40 / 0.72 = 55.56% of equity.
        assert r.subgroup_emphasis == {"low_beta_equities": 40.0}

    def test_a_pin_in_a_small_class_is_not_inflated(self):
        """The old division blew a 5%-of-total gold ask up to 50% of a 10%
        commodity class; the stored number is the customer's own again."""
        from app.domains.profile.services.screen_preference_service import (
            resolve_screen_preferences,
        )

        r = resolve_screen_preferences(
            CLASS_MIX, [{"subgroup": "gold_commodities", "pct_of_total": 5}]
        )
        assert r.subgroup_emphasis == {"gold_commodities": 5.0}

    async def test_the_saved_row_reads_back_as_the_share_that_was_typed(
        self, monkeypatch
    ):
        import datetime
        from types import SimpleNamespace

        import app.domains.profile.services.screen_preference_service as svc
        from app.domains.practical_asset_allocation.services.paa_engine import (
            input_builder,
        )

        async def fake_active(db, uid):
            return SimpleNamespace(
                asset_class_requested=dict(CLASS_MIX),
                resolved_targets={"low_beta_equities": 40.0, "gold_commodities": 5.0},
                activated_at=datetime.datetime(2026, 9, 14, tzinfo=datetime.timezone.utc),
            )

        monkeypatch.setattr(svc, "active_preference_row", fake_active)
        monkeypatch.setattr(svc, "_build_ctx", lambda user: None)
        monkeypatch.setattr(
            input_builder,
            "build_practical_allocation_input_for_user",
            lambda ctx, apply_saved_preferences: (make_practical_input(), None),
        )

        out = await svc.screen_read_model(db=None, user=SimpleNamespace(id=1))
        pins = {p.subgroup: p.pct_of_total for p in out.saved.pins}
        # Old basis multiplied back by the class share: 40 x 0.72 = 28.8.
        assert pins == {"low_beta_equities": 40.0, "gold_commodities": 5.0}


class TestTheEnginePinsAgainstTheWholePortfolio:
    def test_a_share_converts_against_total_corpus(self):
        from practical_asset_allocation.human_override import HumanOverridePreferences
        from practical_asset_allocation.pipeline import _subgroup_pins

        pins, excluded, gold_excluded = _subgroup_pins(
            HumanOverridePreferences(subgroup_emphasis={"low_beta_equities": 40.0}),
            20_000_000,
        )
        assert pins == {"low_beta_equities": 8_000_000}
        assert excluded == frozenset() and gold_excluded is False

    def test_the_class_probe_run_is_gone(self):
        """The probe existed only to read the class amounts a %-of-class pin
        had to be measured against. With the basis at % of total the long-term
        step runs ONCE per allocation, asks or no asks."""
        from practical_asset_allocation import pipeline
        from practical_asset_allocation.human_override import HumanOverridePreferences

        calls = []
        real = pipeline._run_practical_long_term

        def counting(*args, **kwargs):
            calls.append(kwargs)
            return real(*args, **kwargs)

        pipeline._run_practical_long_term = counting
        try:
            pipeline.run_practical_allocation(
                make_practical_input().model_copy(
                    update={
                        "human_override": HumanOverridePreferences(
                            asset_class_requested=dict(CLASS_MIX),
                            subgroup_emphasis={"low_beta_equities": 40.0},
                        )
                    }
                )
            )
        finally:
            pipeline._run_practical_long_term = real
        assert len(calls) == 1, f"long-term step ran {len(calls)}x, expected once"


def test_a_forty_percent_pin_lands_at_forty_percent_of_total():
    """PROOF (spec §7): saved as 40% of total -> ₹80,00,000 of a ₹2cr
    portfolio -> that is what the row holds."""
    from app.domains.profile.services.screen_preference_service import (
        resolve_screen_preferences,
    )
    from practical_asset_allocation.human_override import HumanOverridePreferences
    from practical_asset_allocation.pipeline import run_practical_allocation

    resolved = resolve_screen_preferences(
        CLASS_MIX, [{"subgroup": "low_beta_equities", "pct_of_total": 40}]
    )
    out = run_practical_allocation(
        make_practical_input().model_copy(
            update={
                "human_override": HumanOverridePreferences(
                    asset_class_requested=resolved.asset_class_requested,
                    subgroup_emphasis=resolved.subgroup_emphasis,
                )
            }
        )
    )
    landed = {r.subgroup: r.total for r in out.aggregated_subgroups}
    assert landed["low_beta_equities"] == pytest.approx(8_000_000, abs=100)
    assert landed["low_beta_equities"] * 100.0 / out.grand_total == pytest.approx(
        40.0, abs=0.01
    )


def test_a_pin_needs_no_class_ask_to_mean_what_it_says():
    """The discriminating case. With no class preference the two bases part
    company: 25 used to mean 25% of the ₹90,62,000 equity class — ₹22,65,500,
    11.3% of the portfolio — and now means 25% of the portfolio itself."""
    from practical_asset_allocation.human_override import HumanOverridePreferences
    from practical_asset_allocation.pipeline import run_practical_allocation

    out = run_practical_allocation(
        make_practical_input().model_copy(
            update={
                "human_override": HumanOverridePreferences(
                    subgroup_emphasis={"low_beta_equities": 25.0}
                )
            }
        )
    )
    landed = {r.subgroup: r.total for r in out.aggregated_subgroups}
    assert landed["low_beta_equities"] == pytest.approx(5_000_000, abs=100)


def test_a_pin_is_a_floor_in_the_new_basis_too():
    """D-A5 — a 5%-of-total gold ask (₹10,00,000) is honoured as a floor; the
    old basis read the same 5 as 5% of the commodity class, ₹98,500."""
    from practical_asset_allocation.human_override import HumanOverridePreferences
    from practical_asset_allocation.pipeline import run_practical_allocation

    out = run_practical_allocation(
        make_practical_input().model_copy(
            update={
                "human_override": HumanOverridePreferences(
                    subgroup_emphasis={"gold_commodities": 5.0}
                )
            }
        )
    )
    landed = {r.subgroup: r.total for r in out.aggregated_subgroups}
    assert landed["gold_commodities"] >= 1_000_000


class TestChatTokensStepInPointsOfTotal:
    def test_the_step_sizes_are_numerically_unchanged(self):
        """Decision 2026-09-14: the constants keep their values and are simply
        reinterpreted — a step is 10 points of the portfolio, not of a class."""
        from app.domains.mutual_funds.services import investment_preferences as ip

        assert ip.SUBGROUP_STEP_PP == 10.0
        assert ip.HEAVY_STEP_PP == 20.0
        assert ip.HEAVY_SUBGROUP_FLOOR_PCT == 40.0

    def test_more_steps_ten_points_of_total(self):
        from app.domains.mutual_funds.services.investment_preferences import (
            resolve_saved_preferences,
        )

        # low_beta holds 10.28% of the neutral portfolio -> "more" = 20.28.
        r = resolve_saved_preferences(
            {"subgroups": {"low_beta_equities": "more"}},
            current_class_mix_pct=dict(CLASS_MIX),
            current_subgroup_share_pct={"low_beta_equities": 10.28},
        )
        assert r.subgroup_emphasis == {"low_beta_equities": pytest.approx(20.28)}
        assert r.applied_defaults["low_beta_equities"].endswith("% of portfolio")
