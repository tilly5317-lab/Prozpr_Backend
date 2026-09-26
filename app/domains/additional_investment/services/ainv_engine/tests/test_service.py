"""Additional-investment orchestrator e2e: allocation primed → input built →
pure engine run → facts pack threaded through. Uses plain stand-ins/fakes — no
real DB, no LLM (mirrors rebal_engine/tests/test_service.py's patching style)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.additional_investment.services.ainv_engine.holdings_snapshot import (
    HoldingsSnapshot,
)

ensure_ai_agents_path()


def _empty_snapshot_mock():
    """Deficit-fill loads a holdings snapshot on every lumpsum run; tests that
    aren't about the snapshot patch in an empty one."""
    return AsyncMock(return_value=HoldingsSnapshot())


def _fake_ainv_input(deploy_amount_inr: float):
    """A real AdditionalInvestmentInput the pure engine can run on: one subgroup
    with a long-term amount, one rank-1 fund, 100% cap — so the engine emits a
    single BUY that fully deploys and names the fund."""
    from additional_investment.models import (
        AdditionalInvestmentInput,
        Cadence,
        RankedFund,
        SubgroupBucketAmounts,
    )

    return AdditionalInvestmentInput(
        deploy_amount_inr=deploy_amount_inr,
        cadence=Cadence.LUMPSUM,
        subgroups=[
            SubgroupBucketAmounts(
                subgroup="low_beta_equities",
                emergency=0.0,
                short_term=0.0,
                medium_term=0.0,
                long_term=1_000_000.0,
                total=1_000_000.0,
            ),
        ],
        short_term_fulfilled=True,
        medium_term_fulfilled=True,
        ranked_funds=[
            RankedFund(
                asset_subgroup="low_beta_equities",
                sub_category="Large Cap Fund",
                rank=1,
                isin="INF000000001",
                scheme_code="100001",
                recommended_fund="ICICI Bluechip",
            ),
        ],
        cap_pct_by_subgroup={"low_beta_equities": 100.0},
        default_cap_pct=100.0,
        rounding_multiple_inr=100,
        exclude_subgroups=set(),
    )


@pytest.mark.asyncio
async def test_e2e_buys_name_funds_and_deploy_accounting_balances():
    from additional_investment.models import Cadence
    from app.domains.additional_investment.services.ainv_engine import service as svc

    deploy = 100_000.0
    fake_input = _fake_ainv_input(deploy)
    fake_alloc = SimpleNamespace(
        result=SimpleNamespace(aggregated_subgroups=[]),
        blocking_message=None,
    )
    user = SimpleNamespace(id=uuid.uuid4())

    with patch.object(
        svc, "load_holdings_snapshot", new=_empty_snapshot_mock()
    ), patch.object(
        svc,
        "compute_practical_allocation_result",
        new=AsyncMock(return_value=fake_alloc),
    ), patch.object(
        svc,
        "build_additional_investment_input_for_user",
        new=AsyncMock(return_value=(fake_input, {"debug": "fake"})),
    ):
        outcome = await svc.compute_additional_investment_result(
            user,
            "invest 1 lakh as lumpsum",
            db=SimpleNamespace(),
            acting_user_id=user.id,
            chat_session_id=None,
            deploy_amount_inr=deploy,
            cadence=Cadence.LUMPSUM,
            chat_ctx=SimpleNamespace(),
            persist=False,
        )

    # The real engine ran and named funds.
    assert outcome.output is not None
    assert len(outcome.output.buys) >= 1
    assert all(b.recommended_fund for b in outcome.output.buys)
    assert any(b.recommended_fund == "ICICI Bluechip" for b in outcome.output.buys)

    # Deploy accounting balances exactly: deployed + undeployed == deploy_amount.
    assert outcome.output.deploy_amount_inr == deploy
    assert outcome.output.deployed_inr + outcome.output.undeployed_inr == deploy

    # Persist OFF in Plan 3a → no run id.
    assert outcome.run_id is None
    # Happy path → no gate.
    assert outcome.blocking_message is None


@pytest.mark.asyncio
async def test_blocking_when_allocation_pre_check_fails():
    """When the practical allocation can't be produced, the orchestrator returns
    a blocking outcome (output None, blocking_message set) instead of raising —
    the chat handler relays it. The input builder is never reached, so only the
    primer is patched."""
    from additional_investment.models import Cadence
    from app.domains.additional_investment.services.ainv_engine import service as svc

    blocked_alloc = SimpleNamespace(
        result=None,
        blocking_message="I need your date of birth before I can plan this.",
    )
    user = SimpleNamespace(id=uuid.uuid4())

    with patch.object(
        svc, "load_holdings_snapshot", new=_empty_snapshot_mock()
    ), patch.object(
        svc,
        "compute_practical_allocation_result",
        new=AsyncMock(return_value=blocked_alloc),
    ):
        outcome = await svc.compute_additional_investment_result(
            user,
            "invest 1 lakh",
            db=SimpleNamespace(),
            acting_user_id=user.id,
            chat_session_id=None,
            deploy_amount_inr=100_000.0,
            cadence=Cadence.LUMPSUM,
            chat_ctx=SimpleNamespace(),
            persist=False,
        )

    assert outcome.output is None
    assert outcome.run_id is None
    assert outcome.blocking_message == (
        "I need your date of birth before I can plan this."
    )


def _fake_alloc(*, grand_total=None, with_preference=False):
    result = SimpleNamespace(
        aggregated_subgroups=[],
        human_override_applied=(
            SimpleNamespace(preference_applied=True, shortfall_reason=None)
            if with_preference
            else None
        ),
    )
    if grand_total is not None:
        result.grand_total = grand_total
    return SimpleNamespace(result=result, blocking_message=None)


async def _run_with_builder(svc, builder_mock, *, run_mock=None):
    """Run the orchestrator with a primed allocation and a patched input builder
    (and optionally a patched engine run); return the outcome."""
    from additional_investment.models import Cadence

    user = SimpleNamespace(id=uuid.uuid4())
    patches = [
        patch.object(svc, "load_holdings_snapshot", new=_empty_snapshot_mock()),
        patch.object(
            svc,
            "compute_practical_allocation_result",
            new=AsyncMock(return_value=_fake_alloc()),
        ),
        patch.object(svc, "build_additional_investment_input_for_user", new=builder_mock),
    ]
    if run_mock is not None:
        patches.append(patch.object(svc, "run_additional_investment", new=run_mock))
    import contextlib

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        return await svc.compute_additional_investment_result(
            user,
            "invest 1 lakh",
            db=SimpleNamespace(),
            acting_user_id=user.id,
            chat_session_id=None,
            deploy_amount_inr=100000.0,
            cadence=Cadence.LUMPSUM,
            chat_ctx=SimpleNamespace(),
            persist=False,
        )


@pytest.mark.asyncio
async def test_incomplete_profile_dob_returns_blocking():
    """The cashflow goal-funding step raising missing_date_of_birth surfaces the
    DOB profile gate — not a generic error and not a raise."""
    from app.domains.additional_investment.services.ainv_engine import service as svc

    outcome = await _run_with_builder(
        svc, AsyncMock(side_effect=ValueError("missing_date_of_birth"))
    )
    assert outcome.output is None
    assert outcome.blocking_message == svc._MSG_MISSING_DOB


@pytest.mark.asyncio
async def test_incomplete_profile_required_inputs_returns_blocking():
    """missing_required_inputs:<keys> -> the generic complete-your-profile gate."""
    from app.domains.additional_investment.services.ainv_engine import service as svc

    outcome = await _run_with_builder(
        svc, AsyncMock(side_effect=ValueError("missing_required_inputs:monthly_income"))
    )
    assert outcome.output is None
    assert outcome.blocking_message == svc._MSG_INCOMPLETE_PROFILE


@pytest.mark.asyncio
async def test_engine_failure_returns_blocking():
    """An engine crash returns the generic engine-error gate, never raising."""
    from app.domains.additional_investment.services.ainv_engine import service as svc

    outcome = await _run_with_builder(
        svc,
        AsyncMock(return_value=(object(), {})),
        run_mock=MagicMock(side_effect=RuntimeError("boom")),
    )
    assert outcome.output is None
    assert outcome.blocking_message == svc._MSG_ENGINE_ERROR


# ── deficit-fill wiring (lumpsum; spec 2026-07-03) ──────────────────────────
@pytest.mark.asyncio
async def test_lumpsum_pins_corpus_and_passes_map():
    """Lumpsum: snapshot loads, PAA gets the holdings-derived CorpusPin, the
    builder gets the by-subgroup map, and the outcome carries deficit_facts."""
    from additional_investment.models import Cadence
    from app.domains.additional_investment.services.ainv_engine import service as svc

    snapshot = HoldingsSnapshot(
        by_subgroup={
            "low_beta_equities": 400000.0,
            "tax_efficient_equities": 50000.0,
            "non_mf_equities": 30000.0,
        },
        unknown_inr=20000.0,
    )  # total 500k
    user = SimpleNamespace(id=uuid.uuid4())
    paa_mock = AsyncMock(return_value=_fake_alloc())
    builder_mock = AsyncMock(return_value=(_fake_ainv_input(500000.0), {}))

    with patch.object(
        svc, "load_holdings_snapshot", new=AsyncMock(return_value=snapshot)
    ), patch.object(
        svc, "compute_practical_allocation_result", new=paa_mock
    ), patch.object(
        svc, "build_additional_investment_input_for_user", new=builder_mock
    ):
        outcome = await svc.compute_additional_investment_result(
            user,
            "invest 5L",
            db=SimpleNamespace(),
            acting_user_id=user.id,
            chat_session_id=None,
            deploy_amount_inr=500000.0,
            cadence=Cadence.LUMPSUM,
            chat_ctx=SimpleNamespace(),
            persist=False,
        )

    pin = paa_mock.call_args.kwargs["corpus_pin"]
    assert pin.total_corpus == pytest.approx(1000000.0)       # 500k + 5L
    assert pin.elss_corpus == pytest.approx(50000.0)
    assert pin.non_mf_equity_corpus == pytest.approx(30000.0)
    assert pin.mf_corpus == pytest.approx(970000.0)           # total - stocks + X
    assert (
        builder_mock.call_args.kwargs["current_value_by_subgroup"]
        == snapshot.by_subgroup
    )
    assert outcome.deficit_facts is not None


@pytest.mark.asyncio
async def test_sip_takes_no_snapshot_and_no_pin():
    """SIP: no snapshot load, no corpus pin, no map to the builder, no facts."""
    from additional_investment.models import Cadence
    from app.domains.additional_investment.services.ainv_engine import service as svc

    user = SimpleNamespace(id=uuid.uuid4())
    snapshot_mock = AsyncMock()
    paa_mock = AsyncMock(return_value=_fake_alloc())
    sip_input = _fake_ainv_input(25000.0).model_copy(
        update={"cadence": ib_cadence().SIP_MONTHLY}
    )
    builder_mock = AsyncMock(return_value=(sip_input, {}))

    with patch.object(
        svc, "load_holdings_snapshot", new=snapshot_mock
    ), patch.object(
        svc, "compute_practical_allocation_result", new=paa_mock
    ), patch.object(
        svc, "build_additional_investment_input_for_user", new=builder_mock
    ), patch.object(
        svc, "latest_buy_trades_by_subgroup", new=AsyncMock(return_value=None)
    ):
        outcome = await svc.compute_additional_investment_result(
            user,
            "start a sip",
            db=SimpleNamespace(),
            acting_user_id=user.id,
            chat_session_id=None,
            deploy_amount_inr=25000.0,
            cadence=Cadence.SIP_MONTHLY,
            chat_ctx=SimpleNamespace(),
            persist=False,
        )

    snapshot_mock.assert_not_called()
    assert paa_mock.call_args.kwargs["corpus_pin"] is None
    assert builder_mock.call_args.kwargs["current_value_by_subgroup"] is None
    assert outcome.deficit_facts is None


def ib_cadence():
    from additional_investment.models import Cadence

    return Cadence


# ── no-CAMS SIP: sized-allocation fallback (2026-08-09) ─────────────────────
def _sip_empty_target_input(deploy: float):
    """A SIP input whose target bucket (long-term) is empty — the whole
    allocation sits in emergency, as it does for a no-CAMS customer at corpus 0.
    The real engine deploys nothing on this input."""
    from additional_investment.models import (
        AdditionalInvestmentInput,
        Cadence,
        RankedFund,
        SubgroupBucketAmounts,
    )

    return AdditionalInvestmentInput(
        deploy_amount_inr=deploy,
        cadence=Cadence.SIP_MONTHLY,
        subgroups=[
            SubgroupBucketAmounts(
                subgroup="short_debt",
                emergency=deploy,
                short_term=0.0,
                medium_term=0.0,
                long_term=0.0,
                total=deploy,
            ),
        ],
        short_term_fulfilled=True,
        medium_term_fulfilled=True,
        ranked_funds=[
            RankedFund(
                asset_subgroup="short_debt",
                sub_category="Liquid Fund",
                rank=1,
                isin="INF000000010",
                scheme_code="100010",
                recommended_fund="Liquid Fund",
            ),
        ],
        cap_pct_by_subgroup={"short_debt": 100.0},
        default_cap_pct=100.0,
        rounding_multiple_inr=100,
        exclude_subgroups=set(),
    )


def _sip_populated_input(deploy: float):
    """A SIP input whose long-term bucket is populated — what a sizing-corpus
    allocation yields. The real engine names a fund and deploys."""
    from additional_investment.models import (
        AdditionalInvestmentInput,
        Cadence,
        RankedFund,
        SubgroupBucketAmounts,
    )

    return AdditionalInvestmentInput(
        deploy_amount_inr=deploy,
        cadence=Cadence.SIP_MONTHLY,
        subgroups=[
            SubgroupBucketAmounts(
                subgroup="low_beta_equities",
                emergency=0.0,
                short_term=0.0,
                medium_term=0.0,
                long_term=1_000_000.0,
                total=1_000_000.0,
            ),
        ],
        short_term_fulfilled=True,
        medium_term_fulfilled=True,
        ranked_funds=[
            RankedFund(
                asset_subgroup="low_beta_equities",
                sub_category="Large Cap Fund",
                rank=1,
                isin="INF000000011",
                scheme_code="100011",
                recommended_fund="Sized Bluechip",
            ),
        ],
        cap_pct_by_subgroup={"low_beta_equities": 100.0},
        default_cap_pct=100.0,
        rounding_multiple_inr=100,
        exclude_subgroups=set(),
    )


@pytest.mark.asyncio
async def test_sip_with_empty_target_bucket_still_names_funds():
    """No-CAMS SIP: the real-corpus allocation leaves the target bucket empty
    (everything sits in emergency), so the first engine run deploys nothing. The
    orchestrator must recover the ideal mix from a sized allocation and still name
    funds — not hand back an empty plan."""
    from additional_investment.models import Cadence
    from app.domains.additional_investment.services.ainv_engine import service as svc

    deploy = 20_000.0
    user = SimpleNamespace(id=uuid.uuid4())
    builder = AsyncMock(
        side_effect=[
            (_sip_empty_target_input(deploy), {"mode": "real_corpus"}),
            (_sip_populated_input(deploy), {"mode": "sized"}),
        ]
    )
    with patch.object(
        svc, "load_holdings_snapshot", new=_empty_snapshot_mock()
    ), patch.object(
        svc, "compute_practical_allocation_result", new=AsyncMock(return_value=_fake_alloc())
    ), patch.object(
        svc, "build_additional_investment_input_for_user", new=builder
    ), patch.object(
        svc, "latest_buy_trades_by_subgroup", new=AsyncMock(return_value=None)
    ):
        outcome = await svc.compute_additional_investment_result(
            user,
            "start a sip of 20000",
            db=SimpleNamespace(),
            acting_user_id=user.id,
            chat_session_id=None,
            deploy_amount_inr=deploy,
            cadence=Cadence.SIP_MONTHLY,
            chat_ctx=SimpleNamespace(),
            persist=False,
        )

    assert outcome.output is not None
    assert outcome.blocking_message is None
    assert len(outcome.output.buys) >= 1, (
        "SIP should recover funds from a sized allocation, not return an empty plan"
    )


@pytest.mark.asyncio
async def test_funded_sip_with_a_real_corpus_does_not_trigger_sized_fallback():
    """A SIP that already names funds, built from a no-preference allocation,
    must NOT re-run the allocation. The sized fallback has two triggers since
    2026-09-20: an empty plan (`not response.buys`), or a preference-shaped
    SIP whose corpus falls under `_SIP_MIN_FAITHFUL_CORPUS_INR` (₹10,000).
    Neither fires here — the plan already has buys and `_fake_alloc()` carries
    no `human_override_applied` — so funded/CAMS users on a real corpus are
    unaffected."""
    from additional_investment.models import Cadence
    from app.domains.additional_investment.services.ainv_engine import service as svc

    deploy = 25_000.0
    user = SimpleNamespace(id=uuid.uuid4())
    paa_mock = AsyncMock(return_value=_fake_alloc())
    builder = AsyncMock(return_value=(_sip_populated_input(deploy), {"mode": "real"}))
    with patch.object(
        svc, "load_holdings_snapshot", new=_empty_snapshot_mock()
    ), patch.object(
        svc, "compute_practical_allocation_result", new=paa_mock
    ), patch.object(
        svc, "build_additional_investment_input_for_user", new=builder
    ), patch.object(
        svc, "latest_buy_trades_by_subgroup", new=AsyncMock(return_value=None)
    ):
        outcome = await svc.compute_additional_investment_result(
            user,
            "start a sip of 25000",
            db=SimpleNamespace(),
            acting_user_id=user.id,
            chat_session_id=None,
            deploy_amount_inr=deploy,
            cadence=Cadence.SIP_MONTHLY,
            chat_ctx=SimpleNamespace(),
            persist=False,
        )

    assert len(outcome.output.buys) >= 1
    assert builder.await_count == 1  # no sized retry build
    assert paa_mock.await_count == 1  # allocation computed exactly once


def test_sizing_corpus_populates_the_target_bucket():
    """Load-bearing assumption behind the SIP fallback: a real allocation at
    ``_SIP_RATIO_SIZING_CORPUS_INR`` populates the long-term bucket (so subgroup
    ratios exist), whereas at corpus 0 it collapses into emergency. Guards against
    engine drift silently re-breaking the no-CAMS SIP."""
    from practical_asset_allocation.pipeline import (  # type: ignore[import-not-found]
        PracticalAllocationInput,
        run_practical_allocation,
    )
    from app.domains.additional_investment.services.ainv_engine.service import (
        _SIP_RATIO_SIZING_CORPUS_INR,
    )

    def _mk(total_corpus: float) -> "PracticalAllocationInput":
        return PracticalAllocationInput(
            effective_risk_score=5.5,
            age=40,
            annual_income=2_000_000,
            osi=0.0,
            savings_rate_adjustment="none",
            gap_exceeds_3=False,
            shortfall_amount=0.0,
            total_corpus=total_corpus,
            monthly_household_expense=100_000,
            effective_tax_rate=15.0,
            net_financial_assets=total_corpus,
            goals=[],
            mf_corpus=total_corpus,
            non_mf_equity_corpus=0.0,
            elss_corpus=0.0,
            max_non_mf_equity_pct_client_input=None,
        )

    zero = run_practical_allocation(_mk(0.0))
    assert sum(r.long_term for r in zero.aggregated_subgroups) == 0  # the bug's cause

    sized = run_practical_allocation(_mk(_SIP_RATIO_SIZING_CORPUS_INR))
    assert sum(r.long_term for r in sized.aggregated_subgroups) > 0  # ratios exist


# ── focus_category → request_extras (spec 2026-07-04) ──────────────────────
@pytest.mark.asyncio
async def test_focus_category_lands_in_request_extras():
    from additional_investment.models import Cadence
    from app.domains.additional_investment.services.ainv_engine import service as svc

    seen = {}

    async def _fake_persist(db, uid, output, **kwargs):
        seen.update(kwargs)
        return uuid.uuid4()

    with patch.object(
        svc, "load_holdings_snapshot", new=_empty_snapshot_mock()
    ), patch.object(
        svc, "compute_practical_allocation_result",
        new=AsyncMock(return_value=_fake_alloc()),
    ), patch.object(
        svc, "build_additional_investment_input_for_user",
        new=AsyncMock(return_value=(_fake_ainv_input(500000.0), {})),
    ), patch.object(
        svc, "persist_practical_allocation_run",
        new=AsyncMock(return_value=uuid.uuid4()),
    ), patch.object(
        svc, "persist_additional_investment_recommendation", new=_fake_persist,
    ):
        await svc.compute_additional_investment_result(
            SimpleNamespace(id=uuid.uuid4()),
            "5L in smallcap",
            db=SimpleNamespace(),
            acting_user_id=uuid.uuid4(),
            chat_session_id=uuid.uuid4(),
            deploy_amount_inr=500000.0,
            cadence=Cadence.LUMPSUM,
            chat_ctx=SimpleNamespace(),
            persist=True,
            focus_category="Small Cap Fund",
        )

    assert seen["request_extras"]["focus_category"] == "Small Cap Fund"
    assert seen["request_extras"]["deployment_mode"] == "deficit_fill"


@pytest.mark.asyncio
async def test_sip_passes_rebal_buys_to_builder():
    from additional_investment.models import Cadence
    from app.domains.additional_investment.services.ainv_engine import service as svc

    user = SimpleNamespace(id=uuid.uuid4())
    run_id = uuid.uuid4()
    rebal_map = {"low_beta_equities": ["INF000000001"]}
    paa_mock = AsyncMock(return_value=_fake_alloc())
    sip_input = _fake_ainv_input(25000.0).model_copy(
        update={"cadence": ib_cadence().SIP_MONTHLY}
    )
    builder_mock = AsyncMock(return_value=(sip_input, {}))
    read_mock = AsyncMock(return_value=(run_id, rebal_map))

    with patch.object(
        svc, "compute_practical_allocation_result", new=paa_mock
    ), patch.object(
        svc, "build_additional_investment_input_for_user", new=builder_mock
    ), patch.object(
        svc, "latest_buy_trades_by_subgroup", new=read_mock
    ):
        outcome = await svc.compute_additional_investment_result(
            user, "start a sip", db=SimpleNamespace(), acting_user_id=user.id,
            chat_session_id=None, deploy_amount_inr=25000.0,
            cadence=Cadence.SIP_MONTHLY, chat_ctx=SimpleNamespace(), persist=False,
        )

    read_mock.assert_awaited_once()
    assert read_mock.call_args.args[1] == user.id  # acting user id
    assert builder_mock.call_args.kwargs["rebal_buy_isins_by_subgroup"] == rebal_map
    assert outcome.output is not None


@pytest.mark.asyncio
async def test_sip_rebal_read_failure_degrades_to_fallback_not_a_gate():
    from additional_investment.models import Cadence
    from app.domains.additional_investment.services.ainv_engine import service as svc

    user = SimpleNamespace(id=uuid.uuid4())
    paa_mock = AsyncMock(return_value=_fake_alloc())
    sip_input = _fake_ainv_input(25000.0).model_copy(
        update={"cadence": ib_cadence().SIP_MONTHLY}
    )
    builder_mock = AsyncMock(return_value=(sip_input, {}))
    read_mock = AsyncMock(side_effect=RuntimeError("db down"))

    with patch.object(
        svc, "compute_practical_allocation_result", new=paa_mock
    ), patch.object(
        svc, "build_additional_investment_input_for_user", new=builder_mock
    ), patch.object(
        svc, "latest_buy_trades_by_subgroup", new=read_mock
    ):
        outcome = await svc.compute_additional_investment_result(
            user, "start a sip", db=SimpleNamespace(), acting_user_id=user.id,
            chat_session_id=None, deploy_amount_inr=25000.0,
            cadence=Cadence.SIP_MONTHLY, chat_ctx=SimpleNamespace(), persist=False,
        )

    assert outcome.output is not None
    assert outcome.blocking_message is None
    assert builder_mock.call_args.kwargs["rebal_buy_isins_by_subgroup"] is None


@pytest.mark.asyncio
async def test_lumpsum_never_reads_rebalancing():
    from additional_investment.models import Cadence
    from app.domains.additional_investment.services.ainv_engine import service as svc

    user = SimpleNamespace(id=uuid.uuid4())
    paa_mock = AsyncMock(return_value=_fake_alloc())
    builder_mock = AsyncMock(return_value=(_fake_ainv_input(100000.0), {}))
    read_mock = AsyncMock()

    with patch.object(
        svc, "load_holdings_snapshot", new=_empty_snapshot_mock()
    ), patch.object(
        svc, "compute_practical_allocation_result", new=paa_mock
    ), patch.object(
        svc, "build_additional_investment_input_for_user", new=builder_mock
    ), patch.object(
        svc, "latest_buy_trades_by_subgroup", new=read_mock
    ):
        await svc.compute_additional_investment_result(
            user, "invest 1 lakh", db=SimpleNamespace(), acting_user_id=user.id,
            chat_session_id=None, deploy_amount_inr=100000.0,
            cadence=Cadence.LUMPSUM, chat_ctx=SimpleNamespace(), persist=False,
        )

    read_mock.assert_not_called()
    assert builder_mock.call_args.kwargs["rebal_buy_isins_by_subgroup"] is None


# ── tiny-corpus preference SIP: gated rescue floor ─────────────────────────
@pytest.mark.asyncio
async def test_tiny_corpus_preference_sip_triggers_sized_fallback():
    """corpus ₹100 with a preference now produces buys, so `not response.buys`
    no longer fires. The sized re-derivation must still run."""
    from additional_investment.models import Cadence
    from app.domains.additional_investment.services.ainv_engine import service as svc

    deploy = 100.0
    user = SimpleNamespace(id=uuid.uuid4())
    pins = []

    async def _fake_paa(*args, **kwargs):
        pins.append(kwargs.get("corpus_pin"))
        return _fake_alloc(grand_total=100.0, with_preference=True)

    builder_mock = AsyncMock(
        side_effect=[
            (_sip_populated_input(deploy), {"mode": "real"}),
            (_sip_populated_input(deploy), {"mode": "sized"}),
        ]
    )
    with patch.object(
        svc, "load_holdings_snapshot", new=_empty_snapshot_mock()
    ), patch.object(
        svc, "compute_practical_allocation_result", new=_fake_paa
    ), patch.object(
        svc, "build_additional_investment_input_for_user", new=builder_mock
    ), patch.object(
        svc, "latest_buy_trades_by_subgroup", new=AsyncMock(return_value=None)
    ):
        outcome = await svc.compute_additional_investment_result(
            user, "start a sip of 100", db=SimpleNamespace(), acting_user_id=user.id,
            chat_session_id=None, deploy_amount_inr=deploy,
            cadence=Cadence.SIP_MONTHLY, chat_ctx=SimpleNamespace(), persist=False,
        )

    assert len(pins) == 2 and pins[0] is None
    assert pins[1].total_corpus == svc._SIP_RATIO_SIZING_CORPUS_INR
    assert outcome.output is not None


@pytest.mark.asyncio
async def test_rescue_returns_sized_practical_result_not_discarded_original():
    """once the rescue fires, ``practical_result`` must be
    the sized allocation the buys were actually built from, never the discarded
    real-corpus one — the chat layer reads the preference disclosure off it."""
    from additional_investment.models import Cadence
    from app.domains.additional_investment.services.ainv_engine import service as svc

    deploy = 100.0
    user = SimpleNamespace(id=uuid.uuid4())
    original_alloc = _fake_alloc(grand_total=100.0, with_preference=True)
    sized_alloc = _fake_alloc(
        grand_total=svc._SIP_RATIO_SIZING_CORPUS_INR, with_preference=True
    )
    paa_mock = AsyncMock(side_effect=[original_alloc, sized_alloc])
    builder_mock = AsyncMock(
        side_effect=[
            (_sip_populated_input(deploy), {"mode": "real"}),
            (_sip_populated_input(deploy), {"mode": "sized"}),
        ]
    )
    with patch.object(
        svc, "load_holdings_snapshot", new=_empty_snapshot_mock()
    ), patch.object(
        svc, "compute_practical_allocation_result", new=paa_mock
    ), patch.object(
        svc, "build_additional_investment_input_for_user", new=builder_mock
    ), patch.object(
        svc, "latest_buy_trades_by_subgroup", new=AsyncMock(return_value=None)
    ):
        outcome = await svc.compute_additional_investment_result(
            user, "start a sip of 100", db=SimpleNamespace(), acting_user_id=user.id,
            chat_session_id=None, deploy_amount_inr=deploy,
            cadence=Cadence.SIP_MONTHLY, chat_ctx=SimpleNamespace(), persist=False,
        )

    assert outcome.practical_result is sized_alloc.result
    assert outcome.practical_result is not original_alloc.result


@pytest.mark.asyncio
async def test_no_preference_tiny_corpus_does_not_gain_a_new_trigger():
    """Spec §6: no-preference customers are untouched. With buys present and no
    preference, the corpus floor must not fire."""
    from additional_investment.models import Cadence
    from app.domains.additional_investment.services.ainv_engine import service as svc

    deploy = 100.0
    user = SimpleNamespace(id=uuid.uuid4())
    paa_mock = AsyncMock(return_value=_fake_alloc(grand_total=100.0))
    builder = AsyncMock(return_value=(_sip_populated_input(deploy), {"mode": "real"}))
    with patch.object(
        svc, "load_holdings_snapshot", new=_empty_snapshot_mock()
    ), patch.object(
        svc, "compute_practical_allocation_result", new=paa_mock
    ), patch.object(
        svc, "build_additional_investment_input_for_user", new=builder
    ), patch.object(
        svc, "latest_buy_trades_by_subgroup", new=AsyncMock(return_value=None)
    ):
        outcome = await svc.compute_additional_investment_result(
            user, "start a sip of 100", db=SimpleNamespace(), acting_user_id=user.id,
            chat_session_id=None, deploy_amount_inr=deploy,
            cadence=Cadence.SIP_MONTHLY, chat_ctx=SimpleNamespace(), persist=False,
        )

    assert len(outcome.output.buys) >= 1
    assert builder.await_count == 1
    assert paa_mock.await_count == 1


@pytest.mark.asyncio
async def test_preference_sip_records_forced_flags_in_request_extras():
    """SIP + preference-shaped plan -> request_extras carries the record-honesty
    key, so the persisted engine-input dump doesn't silently assert near-term
    goals are funded."""
    from additional_investment.models import Cadence
    from app.domains.additional_investment.services.ainv_engine import service as svc

    seen = {}

    async def _fake_persist(db, uid, output, **kwargs):
        seen.update(kwargs)
        return uuid.uuid4()

    sip_input = _fake_ainv_input(25000.0).model_copy(
        update={"cadence": ib_cadence().SIP_MONTHLY}
    )
    with patch.object(
        svc, "load_holdings_snapshot", new=_empty_snapshot_mock()
    ), patch.object(
        svc, "compute_practical_allocation_result",
        new=AsyncMock(return_value=_fake_alloc(with_preference=True)),
    ), patch.object(
        svc, "build_additional_investment_input_for_user",
        new=AsyncMock(return_value=(sip_input, {})),
    ), patch.object(
        svc, "persist_practical_allocation_run",
        new=AsyncMock(return_value=uuid.uuid4()),
    ), patch.object(
        svc, "persist_additional_investment_recommendation", new=_fake_persist,
    ), patch.object(
        svc, "latest_buy_trades_by_subgroup", new=AsyncMock(return_value=None)
    ), patch.object(
        svc, "set_starting_monthly_investment", new=AsyncMock()
    ), patch.object(
        svc, "mark_cashflow_stale", new=AsyncMock()
    ):
        await svc.compute_additional_investment_result(
            SimpleNamespace(id=uuid.uuid4()),
            "start a sip of 25000",
            db=SimpleNamespace(),
            acting_user_id=uuid.uuid4(),
            chat_session_id=uuid.uuid4(),
            deploy_amount_inr=25000.0,
            cadence=Cadence.SIP_MONTHLY,
            chat_ctx=SimpleNamespace(),
            persist=True,
        )

    assert (
        seen["request_extras"]["goal_funding_flags_forced"]
        == "stated_preference_suspends_carve_outs"
    )
