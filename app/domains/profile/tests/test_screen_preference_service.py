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
