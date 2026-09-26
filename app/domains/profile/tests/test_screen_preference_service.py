"""S4 percentage-screen preference service: resolver, catalog, save orchestration."""

from __future__ import annotations

import pytest

from app.domains.profile.services.screen_preference_service import (
    resolve_screen_preferences,
    ScreenPreferenceError,
)


def test_class_mix_passes_through_as_asset_class_requested():
    r = resolve_screen_preferences({"equity": 72, "debt": 18, "others": 10}, [])
    assert r.asset_class_requested == {"equity": 72.0, "debt": 18.0, "others": 10.0}
    assert r.subgroup_emphasis == {}


def test_pin_pct_of_total_is_stored_verbatim():
    # D-A3: storage and the engine speak % of total too, so a large-cap ask of
    # 25% of the portfolio is stored as 25 (it used to become 25 / 0.72 = 34.7%
    # of the equity class, and be multiplied back on read).
    r = resolve_screen_preferences(
        {"equity": 72, "debt": 18, "others": 10},
        [{"subgroup": "low_beta_equities", "pct_of_total": 25}],
    )
    assert r.subgroup_emphasis["low_beta_equities"] == pytest.approx(25.0, abs=0.1)


def test_class_mix_must_sum_to_100():
    with pytest.raises(ScreenPreferenceError):
        resolve_screen_preferences({"equity": 50, "debt": 18, "others": 10}, [])


def test_pins_may_not_exceed_their_class_share():
    with pytest.raises(ScreenPreferenceError):
        resolve_screen_preferences(
            {"equity": 20, "debt": 70, "others": 10},
            [{"subgroup": "low_beta_equities", "pct_of_total": 25}],  # 25 > 20 equity
        )


def test_pin_with_unsettable_subgroup_is_rejected():
    with pytest.raises(ScreenPreferenceError):
        resolve_screen_preferences(
            {"equity": 72, "debt": 18, "others": 10},
            [{"subgroup": "not_a_real_subgroup", "pct_of_total": 5}],
        )


def test_multi_asset_is_a_settable_pin():
    # D-A2: the sleeve is a sub-group like any other; 18% of total is stored
    # as 18 (CLASS_OF files the sleeve as equity, which only gates the
    # class-share check now — D-A3).
    r = resolve_screen_preferences(
        {"equity": 72, "debt": 18, "others": 10},
        [{"subgroup": "multi_asset", "pct_of_total": 18}],
    )
    assert r.subgroup_emphasis["multi_asset"] == pytest.approx(18.0, abs=0.1)


def test_locked_holding_rows_are_still_rejected():
    # ELSS and direct stock are HOLDINGS the engine cannot trade, not
    # preferences — unblocking the sleeve must not unblock them.
    for sg in ("tax_efficient_equities", "non_mf_equities"):
        with pytest.raises(ScreenPreferenceError):
            resolve_screen_preferences(
                {"equity": 72, "debt": 18, "others": 10},
                [{"subgroup": sg, "pct_of_total": 5}],
            )


async def test_save_is_a_no_op_when_the_payload_is_unchanged(monkeypatch):
    import uuid
    from types import SimpleNamespace

    import app.domains.profile.services.screen_preference_service as svc

    intent = {"class_mix": {"equity": 72, "debt": 18, "others": 10}, "pins": []}
    fired = {"engine": False, "persist": False}

    async def fake_active(db, uid):
        return SimpleNamespace(customer_choices=intent)

    async def fake_run(user, resolved):
        fired["engine"] = True
        return object(), None

    async def fake_persist(*a, **k):
        fired["persist"] = True

    async def fake_refresh(*a, **k):
        pass

    monkeypatch.setattr(svc, "active_preference_row", fake_active)
    monkeypatch.setattr(svc, "_run_preferred", fake_run)
    monkeypatch.setattr(svc, "_persist_confirm", fake_persist)
    monkeypatch.setattr(svc, "_eager_refresh", fake_refresh)

    resp = await svc.save_screen_preference(
        db=None,
        user=SimpleNamespace(id=uuid.uuid4()),
        class_mix={"equity": 72, "debt": 18, "others": 10},
        pins=[],
    )
    assert resp.no_op is True
    assert fired["engine"] is False and fired["persist"] is False


def test_screen_request_parses_mix_and_pins():
    from app.domains.profile.schemas import ScreenPreferenceRequest

    req = ScreenPreferenceRequest.model_validate(
        {
            "class_mix": {"equity": 72, "debt": 18, "others": 10},
            "pins": [{"subgroup": "low_beta_equities", "pct_of_total": 25}],
        }
    )
    assert req.class_mix["equity"] == 72
    assert req.pins[0].subgroup == "low_beta_equities"
    assert req.pins[0].pct_of_total == 25


def test_subcategory_serializes_class_field_by_alias():
    from app.domains.profile.schemas import ScreenSubcategory

    sc = ScreenSubcategory(id="low_beta_equities", label="Large-cap",
                           recommended_pct_of_total=15.0, **{"class": "equity"})
    assert sc.class_ == "equity"
    assert sc.model_dump(by_alias=True)["class"] == "equity"


def _fake_run(grand, subgroups):
    from types import SimpleNamespace

    return SimpleNamespace(
        grand_total=grand,
        aggregated_subgroups=[SimpleNamespace(subgroup=s, total=t) for s, t in subgroups],
    )


def test_catalog_lists_settable_subgroups_with_pct_of_total():
    from app.domains.profile.services.screen_preference_service import subcategory_catalog

    out = _fake_run(1_000_000, [("low_beta_equities", 150_000), ("gold_commodities", 80_000)])
    cat = {c.id: c for c in subcategory_catalog(out)}
    assert cat["low_beta_equities"].recommended_pct_of_total == 15.0
    assert cat["low_beta_equities"].class_ == "equity"
    assert cat["gold_commodities"].class_ == "others"


def test_catalog_offers_multi_asset_and_excludes_frozen_holdings():
    from app.domains.profile.services.screen_preference_service import subcategory_catalog

    out = _fake_run(1_000_000, [("multi_asset", 500_000), ("low_beta_equities", 150_000)])
    cat = {c.id: c for c in subcategory_catalog(out)}
    assert cat["multi_asset"].recommended_pct_of_total == 50.0
    assert "tax_efficient_equities" not in cat and "non_mf_equities" not in cat


async def test_save_translates_persists_and_refreshes(monkeypatch):
    import uuid
    from types import SimpleNamespace

    import app.domains.profile.services.screen_preference_service as svc

    calls: dict = {}

    async def fake_active(db, uid):
        return None

    async def fake_run(user, resolved):
        calls["resolved"] = resolved
        return object(), None  # (preferred, blocking_message)

    async def fake_persist(db, user, resolved, intent, preferred, prior):
        calls["intent"] = intent

    async def fake_refresh(db, user):
        calls["refreshed"] = True

    monkeypatch.setattr(svc, "active_preference_row", fake_active)
    monkeypatch.setattr(svc, "_run_preferred", fake_run)
    monkeypatch.setattr(svc, "_persist_confirm", fake_persist)
    monkeypatch.setattr(svc, "_eager_refresh", fake_refresh)

    resp = await svc.save_screen_preference(
        db=None,
        user=SimpleNamespace(id=uuid.uuid4()),
        class_mix={"equity": 72, "debt": 18, "others": 10},
        pins=[{"subgroup": "low_beta_equities", "pct_of_total": 25}],
    )
    assert resp.ok is True
    assert calls["resolved"].asset_class_requested == {"equity": 72.0, "debt": 18.0, "others": 10.0}
    assert calls["intent"] == {
        "class_mix": {"equity": 72, "debt": 18, "others": 10},
        "pins": [{"subgroup": "low_beta_equities", "pct_of_total": 25}],
    }
    assert calls["refreshed"] is True


async def test_save_returns_blocked_when_engine_blocks(monkeypatch):
    import uuid
    from types import SimpleNamespace

    import app.domains.profile.services.screen_preference_service as svc

    async def fake_active(db, uid):
        return None

    async def fake_run(user, resolved):
        return None, "Zero corpus."

    monkeypatch.setattr(svc, "active_preference_row", fake_active)
    monkeypatch.setattr(svc, "_run_preferred", fake_run)

    resp = await svc.save_screen_preference(
        db=None,
        user=SimpleNamespace(id=uuid.uuid4()),
        class_mix={"equity": 100, "debt": 0, "others": 0},
        pins=[],
    )
    assert resp.blocked == "Zero corpus."
    assert resp.ok is False


# ---------------------------------------------------------------------------
# Read model — where the customer sits today (frontend spec 2026-09-20 §3.1)
# ---------------------------------------------------------------------------


def _snapshot(by_subgroup=None, unknown_inr=0.0):
    from app.domains.additional_investment.services.ainv_engine.holdings_snapshot import (
        HoldingsSnapshot,
    )

    return HoldingsSnapshot(by_subgroup=dict(by_subgroup or {}), unknown_inr=unknown_inr)


def _current(by_subgroup=None, unknown_inr=0.0):
    from app.domains.profile.services.screen_preference_service import current_block

    return current_block(_snapshot(by_subgroup, unknown_inr))


def test_current_holdings_are_shares_of_the_settable_part_and_sum_to_100():
    # D6: the frozen ELSS is dropped and the rest rescaled — 300k + 100k held
    # in settable rows reads 75 / 25, not 60 / 20 of the 500k portfolio.
    cur = _current(
        {"low_beta_equities": 300_000, "short_debt": 100_000, "tax_efficient_equities": 100_000}
    )
    pct = {h.subgroup: h.pct_of_total for h in cur.holdings}
    assert pct["low_beta_equities"] == 75.0
    assert pct["short_debt"] == 25.0
    assert sum(pct.values()) == pytest.approx(100.0)


def test_excluded_pct_is_the_frozen_share_of_the_whole_portfolio():
    # ...BEFORE the rescale: 100k of the 500k held, not of the 400k kept.
    cur = _current(
        {
            "low_beta_equities": 300_000,
            "short_debt": 100_000,
            "tax_efficient_equities": 60_000,
            "non_mf_equities": 40_000,
        }
    )
    assert cur.excluded_pct == 20.0


def test_current_lists_every_settable_row_and_reads_zero_for_one_not_held():
    from app.domains.profile.services.screen_preference_service import (
        _settable_subcategory_ids,
    )

    cur = _current({"low_beta_equities": 100_000})
    assert [h.subgroup for h in cur.holdings] == _settable_subcategory_ids()
    pct = {h.subgroup: h.pct_of_total for h in cur.holdings}
    assert pct["low_beta_equities"] == 100.0
    assert pct["gold_commodities"] == 0.0


def test_held_categories_the_screen_cannot_set_count_as_excluded():
    # Decision 2026-09-26: a dividend-yield fund (settable nowhere on this
    # screen) and value whose metadata never classified are excluded like the
    # frozen rows, not dropped. Dropping them would inflate every settable row
    # and report nothing excluded for a customer 40% in such a fund.
    cur = _current(
        {"low_beta_equities": 200_000, "dividend_equities": 200_000}, unknown_inr=100_000
    )
    pct = {h.subgroup: h.pct_of_total for h in cur.holdings}
    assert pct["low_beta_equities"] == 100.0
    assert cur.excluded_pct == 60.0


def test_current_is_empty_when_nothing_settable_is_held():
    # 100% ELSS: no settable part to distribute, so `holdings` is empty (the
    # screen reads that as "nothing to show") and everything is excluded.
    cur = _current({"tax_efficient_equities": 100_000})
    assert cur.holdings == []
    assert cur.excluded_pct == 100.0


def test_current_is_empty_and_excludes_nothing_when_nothing_is_held():
    cur = _current({})
    assert cur.holdings == []
    assert cur.excluded_pct == 0.0


def test_current_shares_are_rounded_to_a_tenth():
    # The catalog's precision; the frontend re-spreads the tenths to 100.
    cur = _current({"low_beta_equities": 1, "short_debt": 1, "gold_commodities": 1})
    pct = {h.subgroup: h.pct_of_total for h in cur.holdings}
    assert pct["low_beta_equities"] == 33.3


def test_current_is_optional_on_the_get_response():
    # Frontend spec 2026-09-20 D8: absent, null and empty all read as "no today".
    from app.domains.profile.schemas import ScreenPreferenceGetResponse

    resp = ScreenPreferenceGetResponse(recommendation={"class_mix": {}}, subcategories=[])
    assert resp.current is None
    assert "current" in resp.model_dump()
