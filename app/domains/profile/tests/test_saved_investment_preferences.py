"""S1 storage tests. sqlite: create ONLY the table under test; letter-bearing UUIDs."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

import app.all_models  # noqa: F401  -- registers FK target tables (users) with Base.metadata


USER_ID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeffff0001")


# Contingency (house pattern, cf. the ARRAY→JSON memory note): if sqlite
# refuses to compile JSONB on table-create, add ONCE at the top of this file:
#
#   from sqlalchemy.ext.compiler import compiles
#   from sqlalchemy.dialects.postgresql import JSONB
#
#   @compiles(JSONB, "sqlite")
#   def _jsonb_sqlite(type_, compiler, **kw):
#       return "JSON"


@pytest.fixture
async def session():
    from app.domains.profile.models.saved_investment_preference import (
        SavedInvestmentPreference,
    )

    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(SavedInvestmentPreference.__table__.create)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_round_trip_and_unique_per_user(session):
    from app.domains.profile.models.saved_investment_preference import (
        SavedInvestmentPreference,
    )

    row = SavedInvestmentPreference(
        user_id=USER_ID,
        equity_requested_pct=80.0, debt_requested_pct=15.0, others_requested_pct=5.0,
        equity_target_pct=78.4, debt_target_pct=16.2, others_target_pct=5.4,
        customer_choices={"aggressiveness": "more"},
    )
    session.add(row)
    await session.flush()
    got = await session.get(SavedInvestmentPreference, row.id)
    assert got.asset_class_requested["equity"] == 80.0
    assert got.asset_class_target["equity"] == 78.4
    # Versioned rows: a second INACTIVE row for the same user is history —
    # allowed; a second ACTIVE row violates the partial unique index.
    session.add(SavedInvestmentPreference(user_id=USER_ID, is_active=False))
    await session.flush()
    dup = SavedInvestmentPreference(user_id=USER_ID)
    session.add(dup)
    with pytest.raises(Exception):
        await session.flush()


class TestResolver:
    CUR = {"equity": 70.0, "debt": 25.0, "others": 5.0}
    BETA = {
        "low_beta_equities": 40.0,
        "medium_beta_equities": 40.0,
        "high_beta_equities": 20.0,
    }

    def _resolve(self, intent):
        from app.domains.mutual_funds.services.investment_preferences import (
            resolve_saved_preferences,
        )

        return resolve_saved_preferences(
            intent,
            current_class_mix_pct=self.CUR,
            current_subgroup_share_pct=self.BETA,
        )

    def test_more_equity_applies_default_step(self):
        r = self._resolve({"asset_class": {"class": "equity", "direction": "more"}})
        assert r.asset_class_requested["equity"] == 80.0
        assert "asset_class" in r.applied_defaults

    def test_class_heavy_lifts_to_dominant_floor(self):
        # equity 70 -> heavy = max(60, 70+20) = 90: heavy always beats "more" (+10)
        r = self._resolve({"asset_class": {"class": "equity", "direction": "heavy"}})
        assert r.asset_class_requested["equity"] == 90.0
        r2 = self._resolve({"asset_class": {"class": "others", "direction": "heavy"}})
        assert r2.asset_class_requested["others"] == 60.0  # 5 -> floor 60

    def test_class_less_steps_down(self):
        r = self._resolve({"asset_class": {"class": "equity", "direction": "less"}})
        assert r.asset_class_requested["equity"] == 60.0

    def test_class_none_is_zero(self):
        r = self._resolve({"asset_class": {"class": "others", "direction": "none"}})
        assert r.asset_class_requested["others"] == 0.0
        assert abs(sum(r.asset_class_requested.values()) - 100.0) < 0.01

    def test_explicit_target_used_verbatim(self):
        r = self._resolve({
            "asset_class": {"class": "equity", "direction": "target", "target_pct": 90.0}
        })
        assert r.asset_class_requested["equity"] == 90.0
        assert "asset_class" not in r.applied_defaults

    def test_heavy_token_lifts_to_class_floor(self):
        # "smallcap heavy": high_beta at 20% of equity -> lifted to the 40%
        # class floor. One facet, one vocabulary.
        r = self._resolve({"subgroups": {"high_beta_equities": "heavy"}})
        assert r.subgroup_emphasis == {"high_beta_equities": 40.0}

    def test_heavy_token_above_floor_steps_twenty(self):
        # low_beta already 40% (= floor) -> heavy = max(40, 40+20) = 60, not 50
        r = self._resolve({"subgroups": {"low_beta_equities": "heavy"}})
        assert r.subgroup_emphasis == {"low_beta_equities": 60.0}

    def test_more_token_steps_up_from_current_share(self):
        r = self._resolve({"subgroups": {"medium_beta_equities": "more"}})
        assert r.subgroup_emphasis == {"medium_beta_equities": 50.0}

    def test_less_token_steps_down_floored_at_zero(self):
        r = self._resolve({"subgroups": {
            "medium_beta_equities": "less",   # 40 -> 30
            "high_beta_equities": "less",     # 20 -> 10
        }})
        assert r.subgroup_emphasis == {
            "medium_beta_equities": 30.0,
            "high_beta_equities": 10.0,
        }
        r2 = self._resolve({"subgroups": {"value_equities": "less"}})  # 0 -> 0
        assert r2.subgroup_emphasis == {"value_equities": 0.0}

    def test_none_token_is_hard_zero(self):
        r = self._resolve({"subgroups": {
            "sector_equities": "none",
            "value_equities": 30.0,
        }})
        assert r.subgroup_emphasis == {
            "sector_equities": 0.0,
            "value_equities": 30.0,
        }

    def test_explicit_number_stored_verbatim(self):
        r = self._resolve({"subgroups": {"gold_commodities": 35}})
        assert r.subgroup_emphasis == {"gold_commodities": 35.0}

    def test_unknown_token_rejected(self):
        import pytest

        with pytest.raises(ValueError):
            self._resolve({"subgroups": {"value_equities": "maximum"}})

    def test_empty_intent_resolves_empty(self):
        r = self._resolve({})
        assert r.asset_class_requested is None
        assert r.subgroup_emphasis == {}


class TestSoleClassRouting:
    """A subgroup that is the ONLY settable subgroup of its class (gold in
    'others') is a class control — 'more gold' must increase the others
    allocation, not no-op within a one-category class (demo finding)."""

    def _route(self, intent):
        from app.domains.profile.services.preference_save_service import (
            _route_sole_class_subgroups,
        )
        return _route_sole_class_subgroups(intent)

    def test_more_gold_becomes_more_others_class(self):
        out = self._route({"subgroups": {"gold_commodities": "more"}})
        assert out == {"asset_class": {"class": "others", "direction": "more"}}

    def test_none_gold_becomes_others_none(self):
        out = self._route({"subgroups": {"gold_commodities": "none"}})
        assert out == {"asset_class": {"class": "others", "direction": "none"}}

    def test_heavy_and_less_gold_route_to_class_too(self):
        assert self._route({"subgroups": {"gold_commodities": "heavy"}}) == {
            "asset_class": {"class": "others", "direction": "heavy"}
        }
        assert self._route({"subgroups": {"gold_commodities": "less"}}) == {
            "asset_class": {"class": "others", "direction": "less"}
        }

    def test_number_gold_becomes_others_target(self):
        out = self._route({"subgroups": {"gold_commodities": 15}})
        assert out == {"asset_class": {"class": "others", "direction": "target",
                                       "target_pct": 15.0}}

    def test_gold_alongside_other_subgroups_routes_only_gold(self):
        out = self._route({"subgroups": {"gold_commodities": "more",
                                         "us_equities": "none"}})
        assert out["asset_class"] == {"class": "others", "direction": "more"}
        assert out["subgroups"] == {"us_equities": "none"}

    def test_explicit_asset_class_present_leaves_gold_as_subgroup(self):
        intent = {"asset_class": {"class": "equity", "direction": "more"},
                  "subgroups": {"gold_commodities": "more"}}
        assert self._route(intent) == intent  # untouched — avoid class conflict

    def test_equity_subgroup_not_routed(self):
        intent = {"subgroups": {"high_beta_equities": "more"}}
        assert self._route(intent) == intent  # equity has many subgroups


class TestIntentValidation:
    """I4: malformed intent is rejected at the schema (HTTP 422), never a 500
    mid-resolution."""

    def _valid(self, **kw):
        from app.domains.profile.schemas import InvestmentPreferenceIntent
        return InvestmentPreferenceIntent(**kw)

    def test_unknown_subgroup_token_rejected(self):
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            self._valid(subgroups={"high_beta_equities": "maximum"})

    def test_bad_asset_class_shape_rejected(self):
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            self._valid(asset_class={"direction": "more"})  # no "class"
        with pytest.raises(ValidationError):
            self._valid(asset_class={"class": "stocks"})  # not an asset class

    def test_out_of_range_number_rejected(self):
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            self._valid(subgroups={"gold_commodities": 140})

    def test_five_state_class_directions_accepted_and_junk_rejected(self):
        import pytest
        from pydantic import ValidationError

        for d in ("more", "heavy", "less", "none"):
            self._valid(asset_class={"class": "debt", "direction": d})
        with pytest.raises(ValidationError):
            self._valid(asset_class={"class": "debt", "direction": "maximum"})

    def test_valid_payloads_accepted(self):
        self._valid(asset_class={"class": "equity", "direction": "more"})
        self._valid(asset_class={"class": "debt", "direction": "target", "target_pct": 30})
        self._valid(subgroups={"high_beta_equities": "heavy", "us_equities": "none",
                               "gold_commodities": 25})


class _StubUser:
    """Only the attributes build_goal_allocation_input_for_user getattr()s."""

    date_of_birth = None
    effective_risk_assessment = None
    investment_profile = None
    personal_finance_profile = None
    risk_profile = None
    tax_profile = None
    financial_goals: list = []
    portfolios: list = []
    saved_investment_preference = None


def _ctx(user, overrides=None):
    import dataclasses
    from app.domains.ai_engine.turn_context import TurnContext

    fields = {f.name for f in dataclasses.fields(TurnContext)}
    kwargs = {}
    if "user_ctx" in fields:
        kwargs["user_ctx"] = user
    if "chat_overrides" in fields:
        kwargs["chat_overrides"] = overrides
    # Fill any other required fields with None (TurnContext is a dataclass;
    # inspect it once and extend here if construction fails).
    for f in dataclasses.fields(TurnContext):
        if f.name not in kwargs and f.default is dataclasses.MISSING \
                and f.default_factory is dataclasses.MISSING:  # type: ignore[misc]
            kwargs[f.name] = None
    return TurnContext(**kwargs)


class TestLoadPoint:
    def _saved_row(self):
        from app.domains.profile.models.saved_investment_preference import (
            SavedInvestmentPreference,
        )

        return SavedInvestmentPreference(
            user_id=USER_ID,
            equity_requested_pct=80.0,
            debt_requested_pct=15.0,
            others_requested_pct=5.0,
        )

    def test_saved_pref_lands_on_practical_input(self):
        from app.domains.practical_asset_allocation.services.paa_engine.input_builder import (
            build_practical_allocation_input_for_user,
        )

        user = _StubUser()
        user.saved_investment_preference = self._saved_row()
        inp, _ = build_practical_allocation_input_for_user(_ctx(user))
        assert inp.human_override is not None
        assert inp.human_override.asset_class_requested["equity"] == 80.0

    def test_bypass_knob_returns_neutral(self):
        from app.domains.practical_asset_allocation.services.paa_engine.input_builder import (
            build_practical_allocation_input_for_user,
        )

        user = _StubUser()
        user.saved_investment_preference = self._saved_row()
        inp, _ = build_practical_allocation_input_for_user(
            _ctx(user), apply_saved_preferences=False
        )
        assert inp.human_override is None

    def test_one_off_override_wins_field_level(self):
        from app.domains.practical_asset_allocation.services.paa_engine.input_builder import (
            build_practical_allocation_input_for_user,
        )

        user = _StubUser()
        user.saved_investment_preference = self._saved_row()
        overrides = {
            "human_override_preferences": {
                "asset_class_requested": {"equity": 100.0, "debt": 0.0, "others": 0.0}
            }
        }
        inp, _ = build_practical_allocation_input_for_user(_ctx(user, overrides))
        assert inp.human_override.asset_class_requested["equity"] == 100.0

    def test_no_pref_no_override_is_none(self):
        from app.domains.practical_asset_allocation.services.paa_engine.input_builder import (
            build_practical_allocation_input_for_user,
        )

        inp, _ = build_practical_allocation_input_for_user(_ctx(_StubUser()))
        assert inp.human_override is None


class TestLoadHumanOverrideForUser:
    """Task 10 refactor: the saved-row → HumanOverridePreferences mapping,
    extracted out of build_practical_allocation_input_for_user so service.py
    can reuse it for ideal-parity."""

    def _saved_row(self):
        from app.domains.profile.models.saved_investment_preference import (
            SavedInvestmentPreference,
        )

        return SavedInvestmentPreference(
            user_id=USER_ID,
            equity_requested_pct=80.0,
            debt_requested_pct=15.0,
            others_requested_pct=5.0,
        )

    def test_none_for_user_without_row(self):
        from app.domains.practical_asset_allocation.services.paa_engine.input_builder import (
            load_human_override_for_user,
        )

        assert load_human_override_for_user(_StubUser()) is None

    def test_populated_for_stub_user_with_saved_row(self):
        from app.domains.practical_asset_allocation.services.paa_engine.input_builder import (
            load_human_override_for_user,
        )

        user = _StubUser()
        user.saved_investment_preference = self._saved_row()
        prefs = load_human_override_for_user(user)
        assert prefs is not None
        assert prefs.asset_class_requested["equity"] == 80.0


def test_contract_single_computation_reader():
    """The future-module contract: only the profile domain may import the
    preferences MODEL, and only the sanctioned load/parity points may read
    the ``user.saved_investment_preference`` relationship. (Run tables carry
    a ``saved_investment_preference_id`` FK column — a string FK, no model
    import — so they are exempt by construction.)"""
    import subprocess

    grep_args = [
        "-rln", "--include=*.py",
        "--exclude=test_*.py", "--exclude=conftest.py",
    ]
    class_out = subprocess.run(
        ["grep", *grep_args, "SavedInvestmentPreference", "app/"],
        capture_output=True, text=True,
    ).stdout.splitlines()
    class_allowed = (
        "app/domains/profile/",
        "app/all_models.py",
        "app/domains/identity/models/user.py",
        # Sanctioned load point — names the class in its docstring only.
        "app/domains/practical_asset_allocation/services/paa_engine/input_builder.py",
    )
    offenders = [p for p in class_out if not any(f in p for f in class_allowed)]
    assert not offenders, f"modules must not import the preferences model: {offenders}"

    attr_out = subprocess.run(
        ["grep", *grep_args, r"\.saved_investment_preference\b", "app/"],
        capture_output=True, text=True,
    ).stdout.splitlines()
    attr_allowed = (
        "app/domains/profile/",
        "app/domains/identity/",
        "app/core/",  # user-context eager-loading only
        "app/domains/practical_asset_allocation/services/paa_engine/input_builder.py",
        "app/domains/asset_allocation/services/aa_engine/service.py",
    )
    offenders = [p for p in attr_out if not any(f in p for f in attr_allowed)]
    assert not offenders, f"modules must not read preferences directly: {offenders}"


class TestTagging:
    @pytest.fixture
    async def tag_session(self):
        from app.domains.profile.models.saved_investment_preference import (
            SavedInvestmentPreference,
        )

        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as conn:
            await conn.run_sync(SavedInvestmentPreference.__table__.create)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as s:
            yield s
        await engine.dispose()

    async def test_none_when_not_applied_even_with_active_row(self, tag_session):
        from app.domains.profile.models.saved_investment_preference import (
            SavedInvestmentPreference,
        )
        from app.domains.profile.services.preference_tagging import (
            active_preference_id,
        )

        tag_session.add(SavedInvestmentPreference(user_id=USER_ID))
        await tag_session.flush()
        assert await active_preference_id(tag_session, USER_ID, applied=False) is None

    async def test_returns_active_row_id_never_the_inactive_one(self, tag_session):
        from app.domains.profile.models.saved_investment_preference import (
            SavedInvestmentPreference,
        )
        from app.domains.profile.services.preference_tagging import (
            active_preference_id,
        )

        old = SavedInvestmentPreference(user_id=USER_ID, is_active=False)
        cur = SavedInvestmentPreference(user_id=USER_ID)
        tag_session.add_all([old, cur])
        await tag_session.flush()
        assert await active_preference_id(tag_session, USER_ID, applied=True) == cur.id

    async def test_none_without_any_row(self, tag_session):
        from app.domains.profile.services.preference_tagging import (
            active_preference_id,
        )

        assert await active_preference_id(tag_session, USER_ID, applied=True) is None


async def test_rebalancing_run_carries_preference_fk_column():
    import uuid as _uuid
    from datetime import datetime, timezone

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.domains.rebalancing.models.rebalancing_run import (
        RebalancingRun,
        RebalancingRunStatus,
        TaxRegime,
    )

    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(RebalancingRun.__table__.create)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        run = RebalancingRun(
            user_id=_uuid.uuid4(),
            portfolio_id=_uuid.uuid4(),
            source_allocation_run_id=_uuid.UUID(
                "aaaaaaaa-bbbb-cccc-dddd-eeeeffff0002"
            ),
            status=RebalancingRunStatus.pending,
            engine_request_id=_uuid.uuid4(),
            engine_version="test",
            computed_at=datetime.now(timezone.utc),
            tax_regime=TaxRegime("new"),
            effective_tax_rate_pct=0,
            total_corpus=0,
            rounding_step=100,
            carryforward_st_loss_inr=0,
            carryforward_lt_loss_inr=0,
            knob_snapshot={},
            saved_investment_preference_id=_uuid.UUID(
                "aaaaaaaa-bbbb-cccc-dddd-eeeeffff0009"
            ),
        )
        s.add(run)
        await s.flush()
        run_id = run.id

        got = (
            await s.execute(select(RebalancingRun).where(RebalancingRun.id == run_id))
        ).scalar_one()
        assert got.saved_investment_preference_id == _uuid.UUID(
            "aaaaaaaa-bbbb-cccc-dddd-eeeeffff0009"
        )

        run2 = RebalancingRun(
            user_id=_uuid.uuid4(),
            portfolio_id=_uuid.uuid4(),
            source_allocation_run_id=_uuid.UUID(
                "aaaaaaaa-bbbb-cccc-dddd-eeeeffff0003"
            ),
            status=RebalancingRunStatus.pending,
            engine_request_id=_uuid.uuid4(),
            engine_version="test",
            computed_at=datetime.now(timezone.utc),
            tax_regime=TaxRegime("new"),
            effective_tax_rate_pct=0,
            total_corpus=0,
            rounding_step=100,
            carryforward_st_loss_inr=0,
            carryforward_lt_loss_inr=0,
            knob_snapshot={},
        )
        s.add(run2)
        await s.flush()
        run2_id = run2.id

        got2 = (
            await s.execute(select(RebalancingRun).where(RebalancingRun.id == run2_id))
        ).scalar_one()
        assert got2.saved_investment_preference_id is None
    await engine.dispose()


# ---------------------------------------------------------------------------
# Task 11: save service (preview -> confirm, anti-ratchet, eager refresh)
# ---------------------------------------------------------------------------


async def _make_user(session):
    """Real ``User`` ORM row (letter-bearing UUID, via uuid4) — no relationship
    priming: the heavy engines this service calls (compute_allocation_result /
    compute_rebalancing_result) are expected to bail out cleanly (caught
    internally, or via the mocked seams) rather than actually need the full
    profile graph in these unit tests."""
    from app.domains.identity.models.user import User

    suffix = uuid.uuid4().hex[:8]
    user = User(
        id=uuid.uuid4(),
        email=f"prefs_test_{suffix}@example.com",
        country_code="+91",
        mobile="9999999999",
        phone=f"+91-9999{suffix}",
    )
    session.add(user)
    await session.flush()
    return user


async def _make_user_with_stored_intent(session, intent, **row_fields):
    """``row_fields`` seeds the stored row's already-resolved columns (e.g.
    ``asset_class_requested``) — needed by the F4 field-level-reuse tests,
    which must assert against a specific stored resolved value, not just the
    stored intent."""
    from app.domains.profile.models.saved_investment_preference import (
        SavedInvestmentPreference,
    )

    user = await _make_user(session)
    row = SavedInvestmentPreference(
        user_id=user.id, customer_choices=intent, **row_fields
    )
    session.add(row)
    await session.flush()
    return user


async def _fetch_pref_row(session, user_id):
    from sqlalchemy import select
    from app.domains.profile.models.saved_investment_preference import (
        SavedInvestmentPreference,
    )

    stmt = select(SavedInvestmentPreference).where(
        SavedInvestmentPreference.user_id == user_id,
        SavedInvestmentPreference.is_active.is_(True),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _fetch_all_rows(session, user_id):
    from sqlalchemy import select
    from app.domains.profile.models.saved_investment_preference import (
        SavedInvestmentPreference,
    )

    stmt = (
        select(SavedInvestmentPreference)
        .where(SavedInvestmentPreference.user_id == user_id)
        .order_by(SavedInvestmentPreference.created_at)
    )
    return (await session.execute(stmt)).scalars().all()


async def _noop_coro():
    return None


class _StubApplied:
    def __init__(self, achieved):
        self.achieved = achieved
        self.requested = None
        self.shortfall_reason = None


class _StubRecommended:
    def __init__(self, mix):
        self.equity_total_pct = mix["equity"]
        self.debt_total_pct = mix["debt"]
        self.others_total_pct = mix["others"]


class _StubBreakdown:
    def __init__(self, mix):
        self.recommended = _StubRecommended(mix)


class _StubOutcome:
    """Stand-in for a ``PracticalAllocationOutput`` — carries exactly the two
    attributes the save service reads off a preferred-run result."""

    def __init__(self, achieved, recommended_mix):
        self.human_override_applied = _StubApplied(achieved)
        self.asset_class_breakdown = _StubBreakdown(recommended_mix)


def _fake_engine(monkeypatch, svc, achieved=None, current_class_mix=None):
    """Patch ``_run_preferred`` to return a stub ``(result, blocking_message)``
    practical-run outcome, and ``_current_mixes`` to return a fixture current
    mix (override ``current_class_mix`` for the F4 ratchet scenario, which
    needs the current mix to already reflect a prior bent save)."""
    achieved = achieved or {"equity": 80.0, "debt": 15.0, "others": 5.0}
    class_mix = current_class_mix or {"equity": 70.0, "debt": 25.0, "others": 5.0}
    stub = _StubOutcome(achieved, class_mix)

    async def _run_preferred(user, prefs):
        return stub, None

    async def _current_mixes(db, user, *, need_subgroup_shares):
        return dict(class_mix), dict(svc._FALLBACK_SUBGROUP_SHARES)

    monkeypatch.setattr(svc, "_run_preferred", _run_preferred)
    monkeypatch.setattr(svc, "_current_mixes", _current_mixes)
    return stub


class TestSaveService:
    @pytest.fixture
    async def session(self):
        from app.domains.identity.models.user import User
        from app.domains.profile.models.saved_investment_preference import (
            SavedInvestmentPreference,
        )
        from app.domains.asset_allocation.models.run import AssetAllocationRun
        from app.domains.additional_investment.models.additional_investment_run import (
            AdditionalInvestmentRun,
        )

        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as conn:
            await conn.run_sync(User.__table__.create)
            await conn.run_sync(SavedInvestmentPreference.__table__.create)
            await conn.run_sync(AssetAllocationRun.__table__.create)
            # _eager_refresh's SIP lookup queries this table on every call
            # (not just an error path) — an empty table (no FK targets
            # needed on sqlite DDL) keeps that a clean "no SIP run" instead
            # of an incidental OperationalError.
            await conn.run_sync(AdditionalInvestmentRun.__table__.create)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as s:
            yield s
        await engine.dispose()

    async def test_unchanged_intent_short_circuits_before_resolution(self, session, monkeypatch):
        """THE anti-ratchet test: an identical re-PUT must not resolve, run,
        persist, or refresh."""
        from app.domains.profile.services import preference_save_service as svc

        calls = {"resolve": 0, "run": 0, "refresh": 0}
        monkeypatch.setattr(svc, "resolve_saved_preferences", lambda *a, **k: calls.__setitem__("resolve", calls["resolve"] + 1))
        monkeypatch.setattr(svc, "_run_preferred", lambda *a, **k: calls.__setitem__("run", calls["run"] + 1))
        monkeypatch.setattr(svc, "_eager_refresh", lambda *a, **k: calls.__setitem__("refresh", calls["refresh"] + 1))

        intent = {"asset_class": {"class": "equity", "direction": "more"}}
        user = await _make_user_with_stored_intent(session, intent)
        resp = await svc.preview_or_save(session, user, intent, confirm=True)
        assert resp.no_op is True
        assert calls == {"resolve": 0, "run": 0, "refresh": 0}

    async def test_preview_persists_nothing(self, session, monkeypatch):
        from app.domains.profile.services import preference_save_service as svc
        from app.domains.profile.models.saved_investment_preference import (
            SavedInvestmentPreference,
        )
        from sqlalchemy import select

        _fake_engine(monkeypatch, svc)  # patched _run_preferred returns a stub outcome
        user = await _make_user(session)
        await svc.preview_or_save(
            session, user,
            {"asset_class": {"class": "equity", "direction": "more"}},
            confirm=False,
        )
        rows = (await session.execute(select(SavedInvestmentPreference))).scalars().all()
        assert rows == []

    async def test_confirm_upserts_with_achieved_and_refreshes(self, session, monkeypatch):
        from app.domains.profile.services import preference_save_service as svc

        refreshed = []
        _fake_engine(monkeypatch, svc, achieved={"equity": 78.4, "debt": 16.2, "others": 5.4})
        monkeypatch.setattr(svc, "_eager_refresh",
                            lambda *a, **k: refreshed.append(True) or _noop_coro())
        user = await _make_user(session)
        resp = await svc.preview_or_save(
            session, user,
            {"asset_class": {"class": "equity", "direction": "more"}},
            confirm=True,
        )
        row = await _fetch_pref_row(session, user.id)
        assert row.asset_class_target["equity"] == 78.4
        assert row.asset_class_requested["equity"] == 80.0
        assert row.is_active is True
        assert refreshed, "changed confirm must trigger the eager refresh"

    async def test_second_save_inserts_new_row_and_deactivates_prior(self, session, monkeypatch):
        """Immutable versioned rows: a re-save NEVER mutates the prior row —
        it deactivates it and inserts a fresh one, so run-table FKs keep
        pointing at exactly the values that shaped them."""
        from app.domains.profile.services import preference_save_service as svc

        _fake_engine(monkeypatch, svc, achieved={"equity": 80.0, "debt": 15.0, "others": 5.0})
        monkeypatch.setattr(svc, "_eager_refresh", lambda *a, **k: _noop_coro())
        user = await _make_user(session)
        await svc.preview_or_save(
            session, user,
            {"asset_class": {"class": "equity", "direction": "more"}},
            confirm=True,
        )
        first = await _fetch_pref_row(session, user.id)
        first_id, first_equity = first.id, first.equity_requested_pct

        await svc.preview_or_save(
            session, user,
            {"asset_class": {"class": "equity", "direction": "target", "target_pct": 90.0}},
            confirm=True,
        )
        rows = await _fetch_all_rows(session, user.id)
        assert len(rows) == 2
        active = [r for r in rows if r.is_active]
        assert len(active) == 1 and active[0].id != first_id
        assert active[0].equity_requested_pct == 90.0
        old_row = next(r for r in rows if r.id == first_id)
        assert old_row.is_active is False
        assert old_row.equity_requested_pct == first_equity, "history must not mutate"

    async def test_clear_deactivates_row_keeps_history(self, session, monkeypatch):
        from app.domains.profile.services import preference_save_service as svc

        _fake_engine(monkeypatch, svc)
        user = await _make_user_with_stored_intent(
            session, {"asset_class": {"class": "equity", "direction": "more"}}
        )
        user_id = user.id  # capture before the call: a real (unmocked)
        # _eager_refresh may roll back mid-refresh, which expires `user` —
        # touching user.id afterwards would need its own reload (F1).
        resp = await svc.preview_or_save(session, user, {}, confirm=True)
        assert await _fetch_pref_row(session, user_id) is None, "no ACTIVE row after clear"
        rows = await _fetch_all_rows(session, user_id)
        assert len(rows) == 1 and rows[0].is_active is False, (
            "clear soft-deactivates — the historical row survives for run FKs"
        )

    async def test_blocked_preferred_compute_persists_nothing(self, session, monkeypatch):
        """F3: a blocked practical run (e.g. zero corpus) must leave no row,
        no run, and no refresh — else the stored intent stays permanently
        unchanged and an identical retry is a forever no-op via anti-ratchet."""
        from app.domains.profile.services import preference_save_service as svc

        refreshed = []

        async def _run_preferred(user, prefs):
            return None, "profile incomplete"

        async def _current_mixes(db, user, *, need_subgroup_shares):
            return (
                {"equity": 70.0, "debt": 25.0, "others": 5.0},
                dict(svc._FALLBACK_SUBGROUP_SHARES),
            )

        monkeypatch.setattr(svc, "_run_preferred", _run_preferred)
        monkeypatch.setattr(svc, "_current_mixes", _current_mixes)
        monkeypatch.setattr(
            svc, "_eager_refresh",
            lambda *a, **k: refreshed.append(True) or _noop_coro(),
        )
        user = await _make_user(session)
        resp = await svc.preview_or_save(
            session, user,
            {"asset_class": {"class": "equity", "direction": "more"}},
            confirm=True,
        )
        assert await _fetch_pref_row(session, user.id) is None
        assert resp.no_op is False
        assert resp.shortfall == "profile incomplete"
        assert not refreshed, "a blocked compute must not trigger the eager refresh"

    async def test_blocked_preferred_compute_uses_honest_default_shortfall(self, session, monkeypatch):
        """F3: when the engine's blocking_message is itself None, fall back to
        a fixed honest sentence rather than surfacing a blank/None shortfall."""
        from app.domains.profile.services import preference_save_service as svc

        async def _run_preferred(user, prefs):
            return None, None

        async def _current_mixes(db, user, *, need_subgroup_shares):
            return (
                {"equity": 70.0, "debt": 25.0, "others": 5.0},
                dict(svc._FALLBACK_SUBGROUP_SHARES),
            )

        monkeypatch.setattr(svc, "_run_preferred", _run_preferred)
        monkeypatch.setattr(svc, "_current_mixes", _current_mixes)
        user = await _make_user(session)
        resp = await svc.preview_or_save(
            session, user,
            {"asset_class": {"class": "equity", "direction": "more"}},
            confirm=False,
        )
        assert resp.shortfall == svc._DEFAULT_BLOCKED_SHORTFALL

    async def test_unchanged_facet_reuses_stored_value_not_rebent(self, session, monkeypatch):
        """F4 (spec ruling): re-sending an untouched "more equity" alongside a
        genuinely new subgroup token must NOT re-resolve asset_class against
        the now-already-bent current mix (which would ratchet 80 -> 90) — the
        stored resolved value is reused verbatim."""
        from app.domains.profile.services import preference_save_service as svc

        stored_intent = {"asset_class": {"class": "equity", "direction": "more"}}
        user = await _make_user_with_stored_intent(
            session, stored_intent,
            equity_requested_pct=80.0, debt_requested_pct=15.0, others_requested_pct=5.0,
        )
        # Current baseline already reflects the prior 80% equity save — a
        # naive re-resolve of "more" from here would land on 90.
        _fake_engine(
            monkeypatch, svc,
            achieved={"equity": 80.0, "debt": 15.0, "others": 5.0},
            current_class_mix={"equity": 80.0, "debt": 15.0, "others": 5.0},
        )
        monkeypatch.setattr(svc, "_eager_refresh", lambda *a, **k: _noop_coro())

        new_intent = {**stored_intent, "subgroups": {"high_beta_equities": "heavy"}}
        resp = await svc.preview_or_save(session, user, new_intent, confirm=True)

        row = await _fetch_pref_row(session, user.id)
        assert row.asset_class_requested["equity"] == 80.0, (
            "the untouched asset_class facet must reuse the stored value, not re-resolve to 90"
        )
        assert row.resolved_targets.get("high_beta_equities") is not None, (
            "the genuinely new subgroup token must resolve fresh"
        )

    async def test_unchanged_subgroup_token_reuses_stored_value(self, session, monkeypatch):
        """Per-subgroup idempotence: changing ONE chip must not re-resolve the
        others — an untouched relative token keeps its stored number."""
        from app.domains.profile.services import preference_save_service as svc

        stored_intent = {"subgroups": {"medium_beta_equities": "more"}}
        user = await _make_user_with_stored_intent(
            session, stored_intent,
            resolved_targets={"medium_beta_equities": 50.0},
        )
        _fake_engine(monkeypatch, svc)
        monkeypatch.setattr(svc, "_eager_refresh", lambda *a, **k: _noop_coro())

        new_intent = {"subgroups": {
            "medium_beta_equities": "more",   # untouched
            "us_equities": "none",            # new chip
        }}
        await svc.preview_or_save(session, user, new_intent, confirm=True)
        row = await _fetch_pref_row(session, user.id)
        assert row.resolved_targets["medium_beta_equities"] == 50.0, (
            "untouched 'more' must keep its stored 50, not re-resolve to 60"
        )
        assert row.resolved_targets["us_equities"] == 0.0

    async def test_eager_refresh_rolls_back_before_logging_on_failure(self, session, monkeypatch):
        """F1: a mid-refresh failure must roll back FIRST, or the session's
        transaction is left DEACTIVE and the sibling block (or whatever runs
        next on this session) dies on PendingRollbackError instead of
        running/self-healing next time."""
        from app.domains.profile.services import preference_save_service as svc
        from sqlalchemy import select
        from app.domains.identity.models.user import User

        async def _boom(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            "app.domains.rebalancing.services.rebal_engine.service.compute_rebalancing_result",
            _boom,
        )
        user = await _make_user(session)
        user_id = user.id  # capture before: the rollback under test expires `user`
        # Commit the user row first — db.rollback() inside _eager_refresh
        # rolls back the WHOLE transaction, and _make_user only flushes, so
        # an uncommitted user row would vanish along with it (unrelated to
        # what this test is checking).
        await session.commit()

        await svc._eager_refresh(session, user)

        # The session must still be usable afterwards — a failed refresh
        # that skipped the F1 rollback would leave the transaction DEACTIVE
        # and this would raise PendingRollbackError instead of finding the row.
        got = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
        assert got.id == user_id

    async def test_confirm_emits_capture_preference_saved(self, session, monkeypatch):
        """Task 13: a successful confirm must fire capture_preference_saved
        with the shortfall bool, after the commit (not inside the txn)."""
        from app.domains.profile.services import preference_save_service as svc

        calls = []
        monkeypatch.setattr(
            svc, "capture_preference_saved", lambda **kw: calls.append(kw)
        )
        _fake_engine(monkeypatch, svc, achieved={"equity": 78.4, "debt": 16.2, "others": 5.4})
        monkeypatch.setattr(svc, "_eager_refresh", lambda *a, **k: _noop_coro())
        user = await _make_user(session)
        await svc.preview_or_save(
            session, user,
            {"asset_class": {"class": "equity", "direction": "more"}},
            confirm=True,
        )
        assert len(calls) == 1
        assert calls[0]["shortfall"] is False
        assert isinstance(calls[0]["fields_set"], list) and calls[0]["fields_set"]
        assert isinstance(calls[0]["applied_defaults"], dict)

    async def test_clear_emits_capture_preference_cleared(self, session, monkeypatch):
        """Task 13: a successful clear must fire capture_preference_cleared."""
        from app.domains.profile.services import preference_save_service as svc

        calls = []
        monkeypatch.setattr(
            svc, "capture_preference_cleared", lambda **kw: calls.append(kw)
        )
        _fake_engine(monkeypatch, svc)
        monkeypatch.setattr(svc, "_eager_refresh", lambda *a, **k: _noop_coro())
        user = await _make_user_with_stored_intent(
            session, {"asset_class": {"class": "equity", "direction": "more"}}
        )
        await svc.preview_or_save(session, user, {}, confirm=True)
        assert len(calls) == 1

    async def test_eager_refresh_commits_after_successful_rebalancing_recompute(self, session, monkeypatch):
        """F2: neither compute_rebalancing_result nor
        compute_additional_investment_result commits — _eager_refresh must
        commit itself per successful block, or the refreshed plan is silently
        rolled back at teardown."""
        from app.domains.profile.services import preference_save_service as svc

        calls = {"commit": 0}
        real_commit = session.commit

        async def _counting_commit():
            calls["commit"] += 1
            await real_commit()

        monkeypatch.setattr(session, "commit", _counting_commit)

        async def _noop_rebal(*a, **k):
            return None

        monkeypatch.setattr(
            "app.domains.rebalancing.services.rebal_engine.service.compute_rebalancing_result",
            _noop_rebal,
        )
        user = await _make_user(session)
        await svc._eager_refresh(session, user)
        assert calls["commit"] == 1, "no SIP run exists, so exactly the rebalancing block should commit"


# ---------------------------------------------------------------------------
# C1 fix wave: baseline mix must come from the practical run row (the numbers
# the customer actually sees), not the always-zero asset_allocation_runs
# columns (write_asset_allocation_run.py persists off a renamed pydantic
# field that no longer round-trips — a separate, pre-existing bug not fixed
# here).
# ---------------------------------------------------------------------------


class TestCurrentMixesBaseline:
    @pytest.fixture
    async def session(self):
        from app.domains.identity.models.user import User
        from app.domains.practical_asset_allocation.models.run import (
            PracticalAssetAllocationRun,
        )

        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as conn:
            await conn.run_sync(User.__table__.create)
            await conn.run_sync(PracticalAssetAllocationRun.__table__.create)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as s:
            yield s
        await engine.dispose()

    async def _make_practical_run(self, session, user_id, *, equity, debt, others):
        from app.domains.practical_asset_allocation.models.run import (
            PracticalAssetAllocationRun,
        )

        run = PracticalAssetAllocationRun(
            user_id=user_id,
            client_age=40,
            client_effective_risk_score=50.0,
            total_corpus=1_000_000.0,
            grand_total=1_000_000.0,
            equity_total_pct=equity,
            debt_total_pct=debt,
            others_total_pct=others,
        )
        session.add(run)
        await session.flush()
        return run

    async def test_baseline_reads_practical_run_row_not_zero(self, session):
        from app.domains.profile.services import preference_save_service as svc

        user = await _make_user(session)
        await self._make_practical_run(
            session, user.id, equity=45.31, debt=33.0, others=21.69
        )
        class_mix, _ = await svc._current_mixes(session, user, need_subgroup_shares=False)
        assert class_mix["equity"] == pytest.approx(45.31, abs=0.01)
        assert class_mix["debt"] == pytest.approx(33.0, abs=0.01)
        assert class_mix["others"] == pytest.approx(21.69, abs=0.01)

        from app.domains.mutual_funds.services.investment_preferences import (
            resolve_saved_preferences,
        )

        resolved = resolve_saved_preferences(
            {"asset_class": {"class": "equity", "direction": "more"}},
            current_class_mix_pct=class_mix,
            current_subgroup_share_pct=dict(svc._FALLBACK_SUBGROUP_SHARES),
        )
        # "more equity" must land ABOVE the real baseline, not the stale-zero
        # baseline (which pre-fix resolved to 10/45/45 — LESS equity, not more).
        assert resolved.asset_class_requested["equity"] == pytest.approx(55.31, abs=0.01)

    async def test_unheld_subgroup_resolves_off_zero_not_fallback(self, session, monkeypatch):
        """C2: when a run exists, a subgroup with no row genuinely holds 0% of
        its class — it must NOT inherit the no-run-yet fallback share (which
        would move ~half a sleeve into a category the customer holds none of)."""
        from types import SimpleNamespace
        from app.domains.profile.services import preference_save_service as svc

        user = await _make_user(session)
        rows = [
            SimpleNamespace(subgroup="low_beta_equities", total=1_000_000.0),
            SimpleNamespace(subgroup="short_debt", total=1_000_000.0),
        ]

        async def _fake_compute(*a, **k):
            return SimpleNamespace(
                result=SimpleNamespace(aggregated_subgroups=rows),
                blocking_message=None,
            )

        monkeypatch.setattr(svc, "compute_practical_allocation_result", _fake_compute)
        _, shares = await svc._current_mixes(session, user, need_subgroup_shares=True)
        assert shares.get("high_beta_equities", 0.0) == 0.0, (
            "an unheld subgroup must be 0, not the fabricated fallback"
        )
        assert abs(shares["low_beta_equities"] - 100.0) < 0.1

    async def test_degenerate_zero_row_falls_back_to_neutral_mix(self, session):
        from app.domains.profile.services import preference_save_service as svc

        user = await _make_user(session)
        await self._make_practical_run(
            session, user.id, equity=0.0, debt=0.0, others=0.0
        )
        class_mix, _ = await svc._current_mixes(session, user, need_subgroup_shares=False)
        assert class_mix == svc._FALLBACK_CLASS_MIX

        from app.domains.mutual_funds.services.investment_preferences import (
            resolve_saved_preferences,
        )

        resolved = resolve_saved_preferences(
            {"asset_class": {"class": "equity", "direction": "more"}},
            current_class_mix_pct=class_mix,
            current_subgroup_share_pct=dict(svc._FALLBACK_SUBGROUP_SHARES),
        )
        # Above the fallback equity share (60), never below it.
        assert (
            resolved.asset_class_requested["equity"]
            > svc._FALLBACK_CLASS_MIX["equity"]
        )


# ---------------------------------------------------------------------------
# Task 12: derived freshness check (is_run_fresh) — no stored stale flag
# anywhere; a plan is fresh iff it points at the user's LATEST allocation run.
# ---------------------------------------------------------------------------


class TestIsRunFresh:
    @pytest.fixture
    async def session(self):
        from app.domains.asset_allocation.models.run import AssetAllocationRun
        from app.domains.rebalancing.models.rebalancing_run import RebalancingRun

        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as conn:
            await conn.run_sync(AssetAllocationRun.__table__.create)
            await conn.run_sync(RebalancingRun.__table__.create)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as s:
            yield s
        await engine.dispose()

    async def _make_aa_run(self, session, *, user_id, created_at):
        from app.domains.asset_allocation.models.run import AssetAllocationRun

        run = AssetAllocationRun(
            user_id=user_id,
            client_age=35,
            client_effective_risk_score=50,
            total_corpus=1_000_000,
            grand_total=1_000_000,
            created_at=created_at,
        )
        session.add(run)
        await session.flush()
        return run

    async def _make_rebal_run(self, session, *, user_id, source_allocation_run_id):
        from datetime import datetime, timezone

        from app.domains.rebalancing.models.rebalancing_run import (
            RebalancingRun,
            RebalancingRunStatus,
            TaxRegime,
        )

        run = RebalancingRun(
            user_id=user_id,
            portfolio_id=uuid.uuid4(),
            source_allocation_run_id=source_allocation_run_id,
            status=RebalancingRunStatus.pending,
            engine_request_id=uuid.uuid4(),
            engine_version="test",
            computed_at=datetime.now(timezone.utc),
            tax_regime=TaxRegime("new"),
            effective_tax_rate_pct=0,
            total_corpus=0,
            rounding_step=100,
            carryforward_st_loss_inr=0,
            carryforward_lt_loss_inr=0,
            knob_snapshot={},
        )
        session.add(run)
        await session.flush()
        return run

    async def test_stale_when_plan_points_at_older_allocation_run(self, session):
        from datetime import datetime, timezone

        from app.domains.rebalancing.services.saved_plan_service import is_run_fresh

        user_id = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeffff1001")
        older = await self._make_aa_run(
            session, user_id=user_id, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)
        )
        await self._make_aa_run(
            session, user_id=user_id, created_at=datetime(2026, 1, 2, tzinfo=timezone.utc)
        )
        plan = await self._make_rebal_run(
            session, user_id=user_id, source_allocation_run_id=older.id
        )

        assert await is_run_fresh(session, plan) is False

    async def test_fresh_when_plan_points_at_latest_allocation_run(self, session):
        from datetime import datetime, timezone

        from app.domains.rebalancing.services.saved_plan_service import is_run_fresh

        user_id = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeffff1002")
        await self._make_aa_run(
            session, user_id=user_id, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)
        )
        newer = await self._make_aa_run(
            session, user_id=user_id, created_at=datetime(2026, 1, 2, tzinfo=timezone.utc)
        )
        plan = await self._make_rebal_run(
            session, user_id=user_id, source_allocation_run_id=newer.id
        )

        assert await is_run_fresh(session, plan) is True
