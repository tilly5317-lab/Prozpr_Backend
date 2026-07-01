"""Additional-investment orchestrator.

Mirrors ``rebal_engine.service.compute_rebalancing_result``: primes the
practical (holdings-aware) allocation, materialises the engine input, runs the
pure additional-investment engine on a worker thread, and builds the chat facts
pack. Persistence is gated behind ``persist`` (left OFF in Plan 3a; Plan 3b
flips the default and wires ``persist_additional_investment_recommendation``).

The additional-investment engine follows the *allocation* I/O family — money is
plain ``float`` and the wrappers are ``AdditionalInvestmentInput`` /
``AdditionalInvestmentOutput`` (NOT Rebalancing's ``Decimal`` +
``ComputeRequest``/``Response``) — there is no tax-lot arithmetic here.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.domains.ai_engine.turn_context import TurnContext

from app.domains.ai_engine.common import ensure_ai_agents_path, trace_line
from app.domains.additional_investment.services.ainv_engine.input_builder import (
    build_additional_investment_input_for_user,
)
from app.domains.practical_asset_allocation.services.paa_engine.service import (
    compute_practical_allocation_result,
)
from app.domains.practical_asset_allocation.services.practical_allocation_persist_service import (
    persist_practical_allocation_run,
)
from app.domains.additional_investment.services.additional_investment_persist_service import (
    persist_additional_investment_recommendation,
)

ensure_ai_agents_path()

from additional_investment.models import (  # type: ignore[import-not-found]  # noqa: E402
    AdditionalInvestmentOutput,
    Cadence,
)
from additional_investment.pipeline import (  # type: ignore[import-not-found]  # noqa: E402
    run_additional_investment,
)


logger = logging.getLogger(__name__)


_MSG_ENGINE_ERROR = (
    "I couldn't work out where to invest your money right now. Try again in a "
    "moment, and if it keeps happening let us know via the help option."
)
_MSG_MISSING_DOB = (
    "I need your date of birth to plan this — it anchors which of your goals are "
    "near-term versus long-term. Add it on your profile and ask me again."
)
_MSG_INCOMPLETE_PROFILE = (
    "I need a bit more of your financial profile before I can plan where to put "
    "fresh money. Complete the missing details on your profile and ask me again."
)

# Stamped onto every persisted AdditionalInvestmentRun.engine_version. Bump when
# the additional-investment engine's output contract changes.
AINV_ENGINE_VERSION = "ainv-1.0.0"


@dataclass(frozen=True)
class AdditionalInvestmentRunOutcome:
    """Immutable outcome of one additional-investment orchestration run.

    On the happy path ``output`` is set and ``blocking_message`` is None. When the
    input builder refuses (incomplete profile) or a pre-check fails, ``output`` is
    None and ``blocking_message`` carries the customer-facing gate text — the chat
    handler relays it via ``format_relay_or_canned`` instead of formatting a BUY
    list (so the orchestrator never raises on a gate). The chat handler builds the
    LLM facts pack itself from ``output`` at format time, so the orchestrator does
    not carry one.

    ``run_id`` is None whenever ``persist=False`` (the only mode in Plan 3a);
    Plan 3b flips ``persist`` and fills it with the persisted run's id.
    ``used_cached_allocation`` is always False today — the practical-allocation
    service recomputes fresh each call (no cache layer yet); the field exists
    for parity with ``RebalancingRunOutcome`` and future caching.
    """

    output: "AdditionalInvestmentOutput | None"
    run_id: "uuid.UUID | None" = None
    used_cached_allocation: bool = False
    blocking_message: str | None = None


async def compute_additional_investment_result(
    user,
    user_question: str,
    *,
    db: AsyncSession,
    acting_user_id: uuid.UUID,
    chat_session_id: Optional[uuid.UUID],
    deploy_amount_inr: float,
    cadence: Cadence,
    chat_ctx: "TurnContext",
    persist: bool = False,
) -> AdditionalInvestmentRunOutcome:
    """Prime allocation → build input → run the engine.

    Mirrors ``compute_rebalancing_result``: the practical allocation is primed
    first (its ``aggregated_subgroups`` feed the per-subgroup deploy split; the
    per-fund caps key off the deploy amount, so no corpus total is read), the
    engine input is materialised from that allocation (holding-agnostic — no
    holdings fetch), and the pure engine runs on a worker thread. The chat handler
    builds the LLM facts pack from the returned output at format time.

    Persistence is gated behind ``persist`` (False in Plan 3a; Plan 3b flips the
    default and calls ``persist_additional_investment_recommendation``).
    """
    trace_line("module: additional_investment — start")

    paa_outcome = await compute_practical_allocation_result(
        user,
        user_question,
        chat_ctx=chat_ctx,
    )
    if paa_outcome.result is None:
        # Pre-check failed (practical allocation could not be produced /
        # incomplete profile): return a blocking outcome the chat handler relays
        # via format_relay_or_canned — never an engine BUY list, never a raise.
        return AdditionalInvestmentRunOutcome(
            output=None,
            blocking_message=paa_outcome.blocking_message or _MSG_ENGINE_ERROR,
        )

    try:
        inp, debug = await build_additional_investment_input_for_user(
            chat_ctx,
            paa_outcome.result,
            deploy_amount_inr=deploy_amount_inr,
            cadence=cadence,
        )
    except ValueError as exc:
        # The goal-funding step (cashflow) HARD-REFUSES an incomplete profile,
        # raising missing_date_of_birth / missing_required_inputs:<keys>. Surface
        # a tailored profile-completion gate the handler relays — never a raise.
        code = str(exc)
        message = (
            _MSG_MISSING_DOB
            if "missing_date_of_birth" in code
            else _MSG_INCOMPLETE_PROFILE
        )
        return AdditionalInvestmentRunOutcome(
            output=None, blocking_message=message
        )
    except Exception:  # noqa: BLE001 — any other builder failure → generic gate
        logger.exception("additional_investment input build failed")
        return AdditionalInvestmentRunOutcome(
            output=None, blocking_message=_MSG_ENGINE_ERROR
        )

    trace_line(f"additional_investment input debug: {debug}")

    try:
        response: AdditionalInvestmentOutput = await asyncio.to_thread(
            run_additional_investment,
            inp,
        )
    except Exception:  # noqa: BLE001 — engine failure → generic gate, never a raise
        logger.exception("additional_investment engine run failed")
        return AdditionalInvestmentRunOutcome(
            output=None, blocking_message=_MSG_ENGINE_ERROR
        )

    # Persist the BUY-only recommendation. Gated like compute_rebalancing_result:
    # counterfactual / no-session paths (persist=False or no chat session) skip
    # the write. Money stays float — persist writes Numeric(18,2) directly.
    #
    # source_allocation_run_id (Option B): the practical allocation run the deploy
    # is derived from. compute_practical_allocation_result returns no run id, so we
    # persist the practical run inline here to capture it — the only practical
    # persist in the ainv path (it does not route through paa_engine/chat.py), so
    # no double-write. The id is always produced, so the FK column is NOT NULL.
    run_id: Optional[uuid.UUID] = None
    if persist and chat_session_id is not None:
        # Best-effort (mirrors paa_engine/chat.py): the BUY list is already
        # computed, so a persistence failure is logged loudly (surfaces in alerts)
        # but never denies the user the recommendation. Flush only — the chat
        # router owns the commit.
        try:
            source_allocation_run_id = await persist_practical_allocation_run(
                db,
                user_id=acting_user_id,
                output=paa_outcome.result,
                chat_session_id=chat_session_id,
                user_question=user_question,
            )
            run_id = await persist_additional_investment_recommendation(
                db,
                acting_user_id,
                response,
                source_allocation_run_id=source_allocation_run_id,
                chat_session_id=chat_session_id,
                used_cached_allocation=False,
                user_question=user_question,
                request=inp,
            )
        except Exception:  # noqa: BLE001 — best-effort persist, never blocks the reply
            logger.exception(
                "Failed to persist additional_investment run for session=%s — "
                "returning recommendation anyway; investigate",
                chat_session_id,
            )
            run_id = None

    return AdditionalInvestmentRunOutcome(
        output=response,
        run_id=run_id,
        used_cached_allocation=False,
    )
