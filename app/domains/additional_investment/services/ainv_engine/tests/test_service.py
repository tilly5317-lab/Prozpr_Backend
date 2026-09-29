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
        result=SimpleNamespace(
            aggregated_subgroups=[], corpus_breakdown=_fake_corpus_breakdown()
        ),
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


def _fake_corpus_breakdown(total_corpus_inr=1_000_000, non_mf_equity_input_inr=0):
    """Minimal corpus_breakdown stand-in — the service reads total_corpus_inr and
    non_mf_equity_input_inr off it to size investable_corpus_inr (spec 2026-09-24)."""
    return SimpleNamespace(
        total_corpus_inr=total_corpus_inr,
        non_mf_equity_input_inr=non_mf_equity_input_inr,
    )


def _fake_alloc():
    return SimpleNamespace(
        result=SimpleNamespace(
            aggregated_subgroups=[],
            human_override_applied=None,
            corpus_breakdown=_fake_corpus_breakdown(),
        ),
        blocking_message=None,
    )


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
    assert paa_mock.call_args.kwargs["monthly_sip"] == 25000.0
    assert paa_mock.call_args.kwargs["short_term_holdings"] is None
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
async def test_funded_sip_does_not_trigger_sized_fallback():
    """A SIP whose long-term plan is at least `_SIP_MIN_LONG_TERM_COLUMN_INR` must
    not re-run the allocation."""
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
async def test_focus_category_lands_in_request_extras(monkeypatch):
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
async def test_sip_reads_latest_rebal_run_for_audit_linkage():
    """SIP still reads the latest rebalancing run — but only for the audit linkage
    (sip_rebal_run_id), not to mirror its funds (spec 2026-09-24). The builder no
    longer receives a rebal-buys kwarg."""
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
    assert "rebal_buy_isins_by_subgroup" not in builder_mock.call_args.kwargs
    assert outcome.output is not None


@pytest.mark.asyncio
async def test_sip_rebal_read_failure_does_not_gate():
    """A failed latest-rebal read just drops the audit linkage; the SIP still
    recommends (never gates), since the run no longer drives selection."""
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
    assert "rebal_buy_isins_by_subgroup" not in builder_mock.call_args.kwargs


def _fake_alloc_with_goal_funding(to_goals=0.0, from_corpus=0.0, subgroup="arbitrage"):
    alloc = _fake_alloc()
    alloc.result.goal_funding = SimpleNamespace(
        monthly_sip_to_goals=to_goals, from_corpus=from_corpus, asset_subgroup=subgroup,
        allocated_amount=0, goals=[],
    )
    return alloc


async def _run_sip(svc, paa, builder, deploy):
    from additional_investment.models import Cadence

    user = SimpleNamespace(id=uuid.uuid4())
    with patch.object(svc, "compute_practical_allocation_result", new=paa), patch.object(
        svc, "build_additional_investment_input_for_user", new=builder
    ), patch.object(svc, "latest_buy_trades_by_subgroup", new=AsyncMock(return_value=None)):
        return await svc.compute_additional_investment_result(
            user, "start a sip", db=SimpleNamespace(), acting_user_id=user.id,
            chat_session_id=None, deploy_amount_inr=deploy, cadence=Cadence.SIP_MONTHLY,
            chat_ctx=SimpleNamespace(), persist=False,
        )


@pytest.mark.asyncio
async def test_sip_goal_share_comes_from_the_real_corpus_run():
    from app.domains.additional_investment.services.ainv_engine import service as svc

    builder = AsyncMock(return_value=(_sip_populated_input(25_000.0), {}))
    paa = AsyncMock(return_value=_fake_alloc_with_goal_funding(to_goals=15_000.0))
    await _run_sip(svc, paa, builder, 25_000.0)
    assert builder.call_args.kwargs["goal_share_inr"] == 15_000.0
    assert builder.call_args.kwargs["goal_subgroup"] == "arbitrage"


@pytest.mark.asyncio
async def test_thin_long_term_plan_rebuilds_from_a_sized_run_keeping_the_goal_share():
    from app.domains.additional_investment.services.ainv_engine import service as svc

    paa = AsyncMock(side_effect=[
        _fake_alloc_with_goal_funding(to_goals=5_000.0),
        _fake_alloc_with_goal_funding(to_goals=0.0),
    ])
    builder = AsyncMock(side_effect=[
        (_sip_empty_target_input(20_000.0), {}),
        (_sip_populated_input(20_000.0), {}),
    ])
    outcome = await _run_sip(svc, paa, builder, 20_000.0)
    assert paa.await_count == 2
    assert builder.await_args_list[1].kwargs["goal_share_inr"] == 5_000.0
    assert paa.await_args_list[1].kwargs["monthly_sip"] == 20_000.0
    assert paa.await_args_list[1].kwargs["corpus_pin"].total_corpus == svc._SIP_RATIO_SIZING_CORPUS_INR
    assert len(outcome.output.buys) >= 1


@pytest.mark.asyncio
async def test_sip_entirely_for_goals_needs_no_long_term_plan():
    from app.domains.additional_investment.services.ainv_engine import service as svc

    paa = AsyncMock(return_value=_fake_alloc_with_goal_funding(to_goals=20_000.0))
    builder = AsyncMock(return_value=(_sip_empty_target_input(20_000.0), {}))
    await _run_sip(svc, paa, builder, 20_000.0)
    assert paa.await_count == 1
    assert builder.await_count == 1


@pytest.mark.asyncio
async def test_lumpsum_uses_long_term_holdings_and_facts_count_goal_money():
    from additional_investment.models import (
        AdditionalInvestmentInput,
        Cadence,
        RankedFund,
        SubgroupBucketAmounts,
    )
    from app.domains.additional_investment.services.ainv_engine import service as svc

    snapshot = HoldingsSnapshot(by_subgroup={
        "near_debt": 100_000.0, "arbitrage": 50_000.0, "low_beta_equities": 400_000.0,
    })
    alloc = SimpleNamespace(
        result=SimpleNamespace(
            aggregated_subgroups=[
                SimpleNamespace(subgroup="arbitrage", total=250_000.0, short_term=250_000.0),
                SimpleNamespace(subgroup="low_beta_equities", total=600_000.0, short_term=0.0),
            ],
            goal_funding=SimpleNamespace(
                allocated_amount=250_000, from_corpus=100_000.0, monthly_sip_to_goals=0.0,
                asset_subgroup="arbitrage", goals=[SimpleNamespace(from_holdings=150_000.0)],
            ),
            human_override_applied=None,
            corpus_breakdown=_fake_corpus_breakdown(),
        ),
        blocking_message=None,
    )
    engine_input = AdditionalInvestmentInput(
        deploy_amount_inr=300_000.0, cadence=Cadence.LUMPSUM,
        subgroups=[
            SubgroupBucketAmounts(subgroup="arbitrage", short_term=250_000.0, total=250_000.0),
            SubgroupBucketAmounts(subgroup="low_beta_equities", long_term=600_000.0, total=600_000.0),
        ],
        current_value_by_subgroup={"low_beta_equities": 400_000.0},
        goal_share_inr=100_000.0, goal_subgroup="arbitrage",
        ranked_funds=[
            RankedFund(asset_subgroup="arbitrage", sub_category="Arbitrage Fund", rank=1,
                       isin="INF000000021", scheme_code="100021", recommended_fund="Arb Fund"),
            RankedFund(asset_subgroup="low_beta_equities", sub_category="Large Cap Fund", rank=1,
                       isin="INF000000022", scheme_code="100022", recommended_fund="Bluechip"),
        ],
    )
    paa = AsyncMock(return_value=alloc)
    builder = AsyncMock(return_value=(engine_input, {}))
    user = SimpleNamespace(id=uuid.uuid4())
    with patch.object(svc, "load_holdings_snapshot", new=AsyncMock(return_value=snapshot)), \
            patch.object(svc, "compute_practical_allocation_result", new=paa), \
            patch.object(svc, "build_additional_investment_input_for_user", new=builder):
        outcome = await svc.compute_additional_investment_result(
            user, "invest 3 lakh", db=SimpleNamespace(), acting_user_id=user.id,
            chat_session_id=None, deploy_amount_inr=300_000.0, cadence=Cadence.LUMPSUM,
            chat_ctx=SimpleNamespace(), persist=False,
        )

    assert paa.call_args.kwargs["short_term_holdings"] == 150_000.0
    assert paa.call_args.kwargs["monthly_sip"] is None
    assert builder.call_args.kwargs["current_value_by_subgroup"] == {
        "near_debt": 0.0, "arbitrage": 0.0, "low_beta_equities": 400_000.0,
    }
    assert builder.call_args.kwargs["goal_share_inr"] == 100_000.0
    facts = {f["subgroup"]: f for f in outcome.deficit_facts}
    assert (facts["arbitrage"]["ideal_inr"], facts["arbitrage"]["current_inr"], facts["arbitrage"]["gap_inr"]) == (
        250_000.0, 150_000.0, 100_000.0,
    )
    assert (facts["low_beta_equities"]["ideal_inr"], facts["low_beta_equities"]["current_inr"]) == (
        600_000.0, 400_000.0,
    )
    assert [sg for sg, f in facts.items() if f["goal_row"]] == ["arbitrage"]
    assert (facts["arbitrage"]["buy_inr"], facts["low_beta_equities"]["buy_inr"]) == (
        100_000.0, 200_000.0,
    )


# ── held short-term money: only what the goals use leaves `current` ─────────
_HELD = {
    "near_debt": 500_000.0,
    "short_debt": 500_000.0,
    "low_beta_equities": 400_000.0,
    "arbitrage_plus_income": 300_000.0,
}


def test_long_term_holdings_unchanged_when_goals_use_no_held_money():
    from app.domains.additional_investment.services.ainv_engine.service import _long_term_holdings

    assert _long_term_holdings(_HELD, 0.0) == _HELD


def test_long_term_holdings_removes_goal_money_proportionally():
    from app.domains.additional_investment.services.ainv_engine.service import _long_term_holdings

    assert _long_term_holdings(_HELD, 600_000.0) == {
        "near_debt": 200_000.0,
        "short_debt": 200_000.0,
        "low_beta_equities": 400_000.0,
        "arbitrage_plus_income": 300_000.0,
    }


def test_long_term_holdings_zeroes_short_term_when_goals_use_it_all():
    from app.domains.additional_investment.services.ainv_engine.service import _long_term_holdings

    assert _long_term_holdings(_HELD, 1_500_000.0) == {
        "near_debt": 0.0,
        "short_debt": 0.0,
        "low_beta_equities": 400_000.0,
        "arbitrage_plus_income": 300_000.0,
    }


async def _run_lumpsum_through_real_builder(snapshot, rows, goal_funding, deploy):
    """Lumpsum through the REAL input builder (only the ranking CSV is stubbed), so
    the holdings map the service computes is the one the engine deploys against."""
    from additional_investment.models import Cadence
    from app.domains.additional_investment.services.ainv_engine import input_builder as ib
    from app.domains.additional_investment.services.ainv_engine import service as svc

    alloc = SimpleNamespace(
        result=SimpleNamespace(
            aggregated_subgroups=rows,
            goal_funding=goal_funding,
            human_override_applied=None,
            corpus_breakdown=_fake_corpus_breakdown(),
        ),
        blocking_message=None,
    )
    ranking = {
        r.subgroup: [SimpleNamespace(
            asset_subgroup=r.subgroup, sub_category="Fund", rank=1, isin=f"INF{i:09d}",
            scheme_code=f"{i}", fund_name=f"{r.subgroup} fund",
        )]
        for i, r in enumerate(rows)
    }
    builder = AsyncMock(side_effect=ib.build_additional_investment_input_for_user)
    user = SimpleNamespace(id=uuid.uuid4())
    with patch.object(svc, "load_holdings_snapshot", new=AsyncMock(return_value=snapshot)), \
            patch.object(svc, "compute_practical_allocation_result", new=AsyncMock(return_value=alloc)), \
            patch.object(svc, "build_additional_investment_input_for_user", new=builder), \
            patch.object(ib, "get_fund_ranking", return_value=ranking):
        outcome = await svc.compute_additional_investment_result(
            user, "invest", db=SimpleNamespace(), acting_user_id=user.id,
            chat_session_id=None, deploy_amount_inr=deploy, cadence=Cadence.LUMPSUM,
            chat_ctx=SimpleNamespace(), persist=False,
        )
    return outcome, builder


@pytest.mark.asyncio
async def test_held_debt_beyond_the_goals_still_covers_the_emergency_target():
    """10L held in short_debt, no short-term goals: that money still counts against
    the 3L emergency target, so a 2L lumpsum buys no short_debt (as in 3.4.0)."""
    from additional_investment.models import SubgroupBucketAmounts

    snapshot = HoldingsSnapshot(by_subgroup={
        "short_debt": 1_000_000.0, "low_beta_equities": 2_000_000.0,
    })
    rows = [
        SubgroupBucketAmounts(subgroup="short_debt", emergency=300_000.0, total=300_000.0),
        SubgroupBucketAmounts(subgroup="arbitrage_plus_income", long_term=900_000.0, total=900_000.0),
        SubgroupBucketAmounts(subgroup="low_beta_equities", long_term=2_000_000.0, total=2_000_000.0),
    ]
    no_goals = SimpleNamespace(
        allocated_amount=0, from_corpus=0.0, monthly_sip_to_goals=0.0,
        asset_subgroup="short_debt", goals=[],
    )
    outcome, builder = await _run_lumpsum_through_real_builder(snapshot, rows, no_goals, 200_000.0)

    assert builder.call_args.kwargs["current_value_by_subgroup"] == snapshot.by_subgroup
    assert "short_debt" not in {b.asset_subgroup for b in outcome.output.buys}
    assert outcome.output.deployed_inr == 200_000.0


@pytest.mark.asyncio
async def test_preference_run_counts_full_holdings_as_current():
    """No goal_funding (a preference run): nothing is carved out, so the engine and
    the facts see the full holdings, exactly as in 3.4.0."""
    from additional_investment.models import SubgroupBucketAmounts

    snapshot = HoldingsSnapshot(by_subgroup={
        "short_debt": 100_000.0, "low_beta_equities": 400_000.0,
    })
    rows = [
        SubgroupBucketAmounts(subgroup="short_debt", emergency=300_000.0, total=300_000.0),
        SubgroupBucketAmounts(subgroup="low_beta_equities", long_term=600_000.0, total=600_000.0),
    ]
    outcome, builder = await _run_lumpsum_through_real_builder(snapshot, rows, None, 100_000.0)

    assert builder.call_args.kwargs["current_value_by_subgroup"] == snapshot.by_subgroup
    assert builder.call_args.kwargs["goal_share_inr"] == 0.0
    facts = {f["subgroup"]: f for f in outcome.deficit_facts}
    assert (facts["short_debt"]["ideal_inr"], facts["short_debt"]["current_inr"]) == (
        300_000.0, 100_000.0,
    )
    assert (facts["low_beta_equities"]["ideal_inr"], facts["low_beta_equities"]["current_inr"]) == (
        600_000.0, 400_000.0,
    )
    assert not any(f["goal_row"] for f in outcome.deficit_facts)


@pytest.mark.asyncio
async def test_routed_subgroup_without_goal_money_is_not_a_goal_row():
    """The routed subgroup gets money (step 1's reserve) but the plan holds no goal
    money: a plain row, never the near-term-goals copy."""
    from additional_investment.models import SubgroupBucketAmounts

    snapshot = HoldingsSnapshot(by_subgroup={
        "short_debt": 50_000.0, "low_beta_equities": 950_000.0,
    })
    rows = [
        SubgroupBucketAmounts(subgroup="short_debt", emergency=300_000.0, total=300_000.0),
        SubgroupBucketAmounts(subgroup="low_beta_equities", long_term=900_000.0, total=900_000.0),
    ]
    no_goal_money = SimpleNamespace(
        allocated_amount=0, from_corpus=0.0, monthly_sip_to_goals=0.0,
        asset_subgroup="short_debt", goals=[],
    )
    outcome, _ = await _run_lumpsum_through_real_builder(snapshot, rows, no_goal_money, 300_000.0)

    facts = {f["subgroup"]: f for f in outcome.deficit_facts}
    assert facts["short_debt"]["goal_row"] is False
