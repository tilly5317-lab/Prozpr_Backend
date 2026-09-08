"""Plan 3b Task 4 — persistence wiring in the additional-investment engine service.

Pure-unit: every collaborator (practical allocation + its inline persist that
yields source_allocation_run_id, input builder, engine, persist service) is
stubbed at the service-module namespace,
and ``build_ainv_facts_pack`` at its CHAT-module path (the service imports it
lazily from chat.py — D6), so no DB / LLM / AI_Agents engine actually runs. The
awaited input builder is patched with ``AsyncMock``. We assert only the
persist hand-off — ``persist=True`` + a chat session ⇒ persist called exactly
once with the engine output and the resolved source-allocation id, and the
returned run id flows onto ``AdditionalInvestmentRunOutcome.run_id``;
``persist=False`` skips the write and leaves ``run_id`` ``None``.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.additional_investment.services.ainv_engine.holdings_snapshot import (
    HoldingsSnapshot,
)

ensure_ai_agents_path()

_SVC = "app.domains.additional_investment.services.ainv_engine.service"


def _fake_output():
    """Minimal valid AdditionalInvestmentOutput (empty buys/targets is allowed)."""
    from additional_investment.models import (  # type: ignore[import-not-found]
        AdditionalInvestmentOutput,
        TargetBucket,
        Cadence,
    )

    return AdditionalInvestmentOutput(
        target_bucket=TargetBucket.LONG_TERM,
        cadence=Cadence.LUMPSUM,
        deploy_amount_inr=400000.0,
        deployed_inr=400000.0,
        undeployed_inr=0.0,
        per_subgroup_target=[],
        buys=[],
    )


@pytest.mark.asyncio
async def test_persist_true_calls_persist_once_and_returns_run_id():
    from additional_investment.models import Cadence  # type: ignore[import-not-found]
    from app.domains.additional_investment.services.ainv_engine.service import (
        compute_additional_investment_result,
    )

    run_id = uuid.uuid4()
    source_id = uuid.uuid4()
    session_id = uuid.uuid4()
    user = MagicMock(id=uuid.uuid4())
    output = _fake_output()
    paa_outcome = MagicMock(
        result=MagicMock(aggregated_subgroups=[]), blocking_message=None
    )

    with (
        patch(
            f"{_SVC}.load_holdings_snapshot",
            new=AsyncMock(return_value=HoldingsSnapshot()),
        ),
        patch(
            f"{_SVC}.compute_practical_allocation_result",
            new=AsyncMock(return_value=paa_outcome),
        ),
        patch(
            f"{_SVC}.persist_practical_allocation_run",
            new=AsyncMock(return_value=source_id),
        ),
        patch(
            f"{_SVC}.build_additional_investment_input_for_user",
            new=AsyncMock(return_value=(MagicMock(), {"debug": "x"})),
        ),
        patch(f"{_SVC}.run_additional_investment", new=MagicMock(return_value=output)),
        patch(
            "app.domains.additional_investment.services.ainv_engine.chat."
            "build_ainv_facts_pack",
            new=MagicMock(return_value={}),
        ),
        patch(
            f"{_SVC}.persist_additional_investment_recommendation",
            new=AsyncMock(return_value=run_id),
        ) as persist_mock,
    ):
        outcome = await compute_additional_investment_result(
            user=user,
            user_question="invest 4 lakh lumpsum",
            db=MagicMock(),
            acting_user_id=user.id,
            chat_session_id=session_id,
            deploy_amount_inr=400000.0,
            cadence=Cadence.LUMPSUM,
            persist=True,
            chat_ctx=MagicMock(),
        )

    persist_mock.assert_awaited_once()
    args, kwargs = persist_mock.await_args
    # positional: (db, acting_user_id, output)
    assert args[2] is output
    assert kwargs["source_allocation_run_id"] == source_id
    assert kwargs["chat_session_id"] == session_id
    assert kwargs["user_question"] == "invest 4 lakh lumpsum"

    assert outcome.run_id == run_id
    assert outcome.output is output
    assert outcome.blocking_message is None


@pytest.mark.asyncio
async def test_persist_false_skips_persist_and_run_id_is_none():
    from additional_investment.models import Cadence  # type: ignore[import-not-found]
    from app.domains.additional_investment.services.ainv_engine.service import (
        compute_additional_investment_result,
    )

    user = MagicMock(id=uuid.uuid4())
    output = _fake_output()
    paa_outcome = MagicMock(
        result=MagicMock(aggregated_subgroups=[]), blocking_message=None
    )

    with (
        patch(
            f"{_SVC}.load_holdings_snapshot",
            new=AsyncMock(return_value=HoldingsSnapshot()),
        ),
        patch(
            f"{_SVC}.compute_practical_allocation_result",
            new=AsyncMock(return_value=paa_outcome),
        ),
        patch(
            f"{_SVC}.persist_practical_allocation_run",
            new=AsyncMock(return_value=uuid.uuid4()),
        ),
        patch(
            f"{_SVC}.build_additional_investment_input_for_user",
            new=AsyncMock(return_value=(MagicMock(), {})),
        ),
        patch(f"{_SVC}.run_additional_investment", new=MagicMock(return_value=output)),
        patch(
            "app.domains.additional_investment.services.ainv_engine.chat."
            "build_ainv_facts_pack",
            new=MagicMock(return_value={}),
        ),
        patch(
            f"{_SVC}.persist_additional_investment_recommendation",
            new=AsyncMock(return_value=uuid.uuid4()),
        ) as persist_mock,
    ):
        outcome = await compute_additional_investment_result(
            user=user,
            user_question="what if I invested 4 lakh",
            db=MagicMock(),
            acting_user_id=user.id,
            chat_session_id=uuid.uuid4(),
            deploy_amount_inr=400000.0,
            cadence=Cadence.LUMPSUM,
            persist=False,
            chat_ctx=MagicMock(),
        )

    persist_mock.assert_not_awaited()
    assert outcome.run_id is None
    assert outcome.output is output


@pytest.mark.asyncio
async def test_persist_failure_is_best_effort_and_still_returns_output():
    """A persistence failure must NOT deny the user the already-computed BUY list:
    the persist block is best-effort (mirrors paa_engine), so a raising persist is
    swallowed, run_id stays None, and the engine output is returned unchanged."""
    from additional_investment.models import Cadence  # type: ignore[import-not-found]
    from app.domains.additional_investment.services.ainv_engine.service import (
        compute_additional_investment_result,
    )

    user = MagicMock(id=uuid.uuid4())
    output = _fake_output()
    paa_outcome = MagicMock(
        result=MagicMock(aggregated_subgroups=[]), blocking_message=None
    )

    with (
        patch(
            f"{_SVC}.load_holdings_snapshot",
            new=AsyncMock(return_value=HoldingsSnapshot()),
        ),
        patch(
            f"{_SVC}.compute_practical_allocation_result",
            new=AsyncMock(return_value=paa_outcome),
        ),
        patch(
            f"{_SVC}.persist_practical_allocation_run",
            new=AsyncMock(return_value=uuid.uuid4()),
        ),
        patch(
            f"{_SVC}.build_additional_investment_input_for_user",
            new=AsyncMock(return_value=(MagicMock(), {})),
        ),
        patch(f"{_SVC}.run_additional_investment", new=MagicMock(return_value=output)),
        patch(
            "app.domains.additional_investment.services.ainv_engine.chat."
            "build_ainv_facts_pack",
            new=MagicMock(return_value={}),
        ),
        patch(
            f"{_SVC}.persist_additional_investment_recommendation",
            new=AsyncMock(side_effect=RuntimeError("db exploded")),
        ),
    ):
        outcome = await compute_additional_investment_result(
            user=user,
            user_question="invest 4 lakh lumpsum",
            db=MagicMock(),
            acting_user_id=user.id,
            chat_session_id=uuid.uuid4(),
            deploy_amount_inr=400000.0,
            cadence=Cadence.LUMPSUM,
            persist=True,
            chat_ctx=MagicMock(),
        )

    # Recommendation delivered despite the save failure; no run id, no blocking gate.
    assert outcome.output is output
    assert outcome.run_id is None
    assert outcome.blocking_message is None


@pytest.mark.asyncio
async def test_first_time_deploy_persists_practical_run_for_source_id():
    """First-time deploy (no prior goal allocation): source_allocation_run_id is
    still non-null because the practical run is persisted inline (Option B) and its
    id is forwarded to the additional-investment persist — there is no nullable
    fallback lookup that could return None."""
    from additional_investment.models import Cadence  # type: ignore[import-not-found]
    from app.domains.additional_investment.services.ainv_engine.service import (
        compute_additional_investment_result,
    )

    practical_run_id = uuid.uuid4()
    session_id = uuid.uuid4()
    user = MagicMock(id=uuid.uuid4())
    output = _fake_output()
    paa_outcome = MagicMock(
        result=MagicMock(aggregated_subgroups=[]), blocking_message=None
    )

    with (
        patch(
            f"{_SVC}.load_holdings_snapshot",
            new=AsyncMock(return_value=HoldingsSnapshot()),
        ),
        patch(
            f"{_SVC}.compute_practical_allocation_result",
            new=AsyncMock(return_value=paa_outcome),
        ),
        patch(
            f"{_SVC}.build_additional_investment_input_for_user",
            new=AsyncMock(return_value=(MagicMock(), {})),
        ),
        patch(f"{_SVC}.run_additional_investment", new=MagicMock(return_value=output)),
        patch(
            "app.domains.additional_investment.services.ainv_engine.chat."
            "build_ainv_facts_pack",
            new=MagicMock(return_value={}),
        ),
        patch(
            f"{_SVC}.persist_practical_allocation_run",
            new=AsyncMock(return_value=practical_run_id),
        ) as practical_persist_mock,
        patch(
            f"{_SVC}.persist_additional_investment_recommendation",
            new=AsyncMock(return_value=uuid.uuid4()),
        ) as persist_mock,
    ):
        await compute_additional_investment_result(
            user=user,
            user_question="invest 4 lakh lumpsum",
            db=MagicMock(),
            acting_user_id=user.id,
            chat_session_id=session_id,
            deploy_amount_inr=400000.0,
            cadence=Cadence.LUMPSUM,
            persist=True,
            chat_ctx=MagicMock(),
        )

    # The practical run is persisted inline, and its fresh, non-null id is the
    # source_allocation_run_id handed to the additional-investment persist.
    practical_persist_mock.assert_awaited_once()
    persist_mock.assert_awaited_once()
    _, kwargs = persist_mock.await_args
    assert kwargs["source_allocation_run_id"] == practical_run_id


@pytest.mark.asyncio
async def test_sip_persist_stamps_sip_rebal_run_id_as_str():
    """SIP + rebal-sourced funds: request_extras carries str(run_id) — a raw
    UUID would break the JSONB json.dumps at flush (spec 2026-07-05 / audit F4).
    With no rebal run (read returns None), SIP adds no extras at all."""
    from additional_investment.models import Cadence  # type: ignore[import-not-found]
    from app.domains.additional_investment.services.ainv_engine.service import (
        compute_additional_investment_result,
    )

    session_id = uuid.uuid4()
    rebal_run_id = uuid.uuid4()
    user = MagicMock(id=uuid.uuid4())
    output = _fake_output()
    paa_outcome = MagicMock(
        result=MagicMock(aggregated_subgroups=[]), blocking_message=None
    )

    for read_result, expected_extras in [
        ((rebal_run_id, {"low_beta_equities": ["INF000000001"]}),
         {"sip_rebal_run_id": str(rebal_run_id)}),
        (None, None),
    ]:
        with (
            patch(
                f"{_SVC}.compute_practical_allocation_result",
                new=AsyncMock(return_value=paa_outcome),
            ),
            patch(
                f"{_SVC}.persist_practical_allocation_run",
                new=AsyncMock(return_value=uuid.uuid4()),
            ),
            patch(
                f"{_SVC}.build_additional_investment_input_for_user",
                new=AsyncMock(return_value=(MagicMock(), {"debug": "x"})),
            ),
            patch(
                f"{_SVC}.run_additional_investment",
                new=MagicMock(return_value=output),
            ),
            patch(
                f"{_SVC}.latest_buy_trades_by_subgroup",
                new=AsyncMock(return_value=read_result),
            ),
            patch(
                f"{_SVC}.persist_additional_investment_recommendation",
                new=AsyncMock(return_value=uuid.uuid4()),
            ) as persist_mock,
        ):
            await compute_additional_investment_result(
                user=user,
                user_question="start a 25k sip",
                db=MagicMock(),
                acting_user_id=user.id,
                chat_session_id=session_id,
                deploy_amount_inr=25000.0,
                cadence=Cadence.SIP_MONTHLY,
                persist=True,
                chat_ctx=MagicMock(),
            )

        persist_mock.assert_awaited_once()
        assert (
            persist_mock.await_args.kwargs["request_extras"] == expected_extras
        ), f"read_result={read_result}"
