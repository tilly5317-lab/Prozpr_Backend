"""Run the goal-based allocation pipeline and format results for chat.

Orchestrates: input building, API key resolution, async thread offload,
step-by-step tracing, optional DB persistence, and markdown formatting.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.domains.ai_engine.common import (
    category_for_effective_risk_score,
    format_inr_indian,
    trace_line,
)
from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.chat.services.ai_module_telemetry import record_ai_module_run
from app.domains.portfolio.services.allocation_rollup import current_asset_class_mix

if TYPE_CHECKING:
    from app.domains.ai_engine.turn_context import TurnContext

ensure_ai_agents_path()

from asset_allocation_pydantic.models import AllocationInput, GoalAllocationOutput
from asset_allocation_pydantic.pipeline import run_allocation_with_state

from app.domains.asset_allocation.services.aa_engine.input_builder import (
    build_goal_allocation_input_for_user,
)


def _invoke_pipeline(
    alloc_input: AllocationInput,
) -> tuple[dict[str, Any], GoalAllocationOutput]:
    """Run the 7-step allocation pipeline. Pure Python — no LLM, no credential.

    No ``rationale_fn``. That optional step called Haiku to write per-bucket prose,
    and reaching it required assigning ``os.environ["ANTHROPIC_API_KEY"]`` around
    the call — process-global mutation that races across concurrent turns.

    The prose was also unread on the happy path: ``compute_allocation_result``
    replaces this output with the PRACTICAL allocation for display, and the
    practical pipeline sets no rationales, so ``goals[].rationale`` reached the
    facts pack as ``None`` every time. It appeared only when the practical engine
    threw and the code fell back to showing the ideal output.
    """
    return run_allocation_with_state(alloc_input)


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AllocationRunOutcome:
    """Immutable outcome of one allocation pipeline run."""

    result: GoalAllocationOutput | None
    blocking_message: str | None = None
    asset_allocation_run_id: uuid.UUID | None = None
    rebalancing_recommendation_id: uuid.UUID | None = None
    allocation_snapshot_id: uuid.UUID | None = None


# ---------------------------------------------------------------------------
# Trace / debug helpers
# ---------------------------------------------------------------------------


def _short_json(obj: object, limit: int = 450) -> str:
    """JSON-serialise *obj* and truncate to *limit* chars for trace logs."""
    try:
        s = json.dumps(obj, default=str)
    except TypeError:
        s = str(obj)
    return s[:limit] + "…" if len(s) > limit else s


_STEP_MAP = [
    ("Step 1 (emergency)", "step1_emergency"),
    ("Step 2 (short-term)", "step2_short_term"),
    ("Step 3 (medium-term)", "step3_medium_term"),
    ("Step 4 (long-term)", "step4_long_term"),
    ("Step 5 (aggregation)", "step5_aggregation"),
    ("Step 6 (guardrails)", "step6_guardrails"),
    ("Step 7 (presentation)", "step7_output"),
]


def _summarize_step(label: str, key: str, blob: Any) -> str:
    """One-line summary of a pipeline step for server-side trace logs."""
    try:
        data = blob.model_dump() if hasattr(blob, "model_dump") else blob
    except Exception:
        return f"{label}: {_short_json(blob, 300)}"

    if not isinstance(data, dict):
        return f"{label}: {_short_json(data, 300)}"

    if key == "step1_emergency":
        return (
            f"{label}: emergency={data.get('total_emergency')} "
            f"remaining={data.get('remaining_corpus')}"
        )
    if key in {"step2_short_term", "step3_medium_term"}:
        return (
            f"{label}: goals={len(data.get('goals_allocated', []))} "
            f"allocated={data.get('allocated_amount')} "
            f"remaining={data.get('remaining_corpus')}"
        )
    if key == "step4_long_term":
        return (
            f"{label}: corpus={data.get('total_long_term_corpus')} "
            f"leftover={data.get('leftover_corpus')}"
        )
    if key == "step5_aggregation":
        return (
            f"{label}: rows={len(data.get('rows', []))} "
            f"grand_total={data.get('grand_total')} "
            f"matches={data.get('grand_total_matches_corpus')}"
        )
    if key == "step6_guardrails":
        validation = data.get("validation") or {}
        return (
            f"{label}: all_rules_pass={validation.get('all_rules_pass')} "
            f"violations={len(validation.get('violations_found', []))}"
        )
    if key == "step7_output":
        return (
            f"{label}: grand_total={data.get('grand_total')} "
            f"buckets={len(data.get('bucket_allocations', []))}"
        )
    return f"{label}: {_short_json(data, 380)}"


# ---------------------------------------------------------------------------
# Chat formatting
# ---------------------------------------------------------------------------

_BUCKET_ORDER = ["emergency", "short_term", "medium_term", "long_term"]
_BUCKET_TITLES = {
    "emergency": "Emergency",
    "short_term": "Short-term",
    "medium_term": "Medium-term",
    "long_term": "Long-term",
}


def build_fallback_brief(output: GoalAllocationOutput, spine_mode: str | None) -> str:
    """Render a ``GoalAllocationOutput`` as user-facing markdown."""
    cs = output.client_summary
    lines: list[str] = []

    lines.append(
        f"Here is a **goal-based allocation** using your **effective risk score "
        f"{cs.effective_risk_score:.1f}** (age {cs.age}, {len(cs.goals)} goal"
        f"{'s' if len(cs.goals) != 1 else ''}, corpus INR {output.grand_total:,.0f})."
    )
    lines.append("")

    buckets_by_name = {b.bucket: b for b in output.bucket_allocations}
    for bucket_name in _BUCKET_ORDER:
        b = buckets_by_name.get(bucket_name)
        if b is None or b.allocated_amount <= 0:
            continue
        title = _BUCKET_TITLES[bucket_name]
        lines.append(
            f"**{title} — INR {b.allocated_amount:,.0f}** "
            f"(goal need INR {b.total_goal_amount:,.0f})"
        )
        if b.rationale:
            lines.append(f"_{b.rationale}_")
        for g in b.goals:
            rationale = b.goal_rationales.get(g.goal_name)
            bullet = (
                f"- **{g.goal_name}** — INR {g.amount_needed:,.0f}, "
                f"{g.time_to_goal_months} months"
            )
            lines.append(bullet)
            if rationale:
                lines.append(f"  _{rationale}_")
        lines.append("")

    acb = output.asset_class_breakdown
    recommended = acb.recommended
    lines.append(
        f"**Recommended asset-class mix** — equity {recommended.equity_total_pct:.1f}% "
        f"(INR {recommended.equity_total:,.0f}), debt {recommended.debt_total_pct:.1f}% "
        f"(INR {recommended.debt_total:,.0f}), others {recommended.others_total_pct:.1f}% "
        f"(INR {recommended.others_total:,.0f})."
    )
    lines.append("")

    _BUCKET_ORDER_FOR_BREAKDOWN = ["short_term", "medium_term", "long_term"]
    bucket_splits = {b.bucket: b for b in recommended.per_bucket}
    breakdown_rows = [
        (bucket_name, bucket_splits.get(bucket_name))
        for bucket_name in _BUCKET_ORDER_FOR_BREAKDOWN
        if bucket_splits.get(bucket_name)
        and (
            bucket_splits[bucket_name].equity
            + bucket_splits[bucket_name].debt
            + bucket_splits[bucket_name].others
        )
        > 0
    ]
    if breakdown_rows:
        lines.append("**By horizon**")
        for bucket_name, split in breakdown_rows:
            assert split is not None
            lines.append(
                f"- {_BUCKET_TITLES[bucket_name]}: "
                f"Equity {split.equity_pct:.0f}% (INR {split.equity:,.0f}), "
                f"Debt {split.debt_pct:.0f}% (INR {split.debt:,.0f}), "
                f"Others / Commodity {split.others_pct:.0f}% (INR {split.others:,.0f})"
            )
        lines.append("")

    if output.future_investments_summary:
        lines.append("**Future investments**")
        for fi in output.future_investments_summary:
            bucket_label = _BUCKET_TITLES.get(fi.bucket or "", fi.bucket or "")
            lines.append(
                f"- {bucket_label}: INR {fi.future_investment_amount:,.0f}"
                + (f" — {fi.message}" if fi.message else "")
            )
        lines.append("")

    if spine_mode != "drift_check":
        lines.append(
            "_Check exit loads and tax before switching schemes; general information only._"
        )

    return "\n".join(lines).rstrip() + "\n"


def compute_current_asset_class_mix(user: Any) -> dict[str, Any] | None:
    """Roll the customer's holdings into a true current Equity/Debt/Cash/Others mix.

    Source of truth for what the customer ACTUALLY holds today, distinct from
    the asset_allocation engine's recommended deployment. The Equity/Debt/Others
    buckets are derived LIVE from holdings via ``current_asset_class_mix`` (the
    same shared rollup the dashboard donut uses, so the two agree) — which splits
    blended multi-asset / hybrid funds across asset classes via the central
    look-through. ``Cash`` (a bank balance, with no holding behind it) is carried
    from the persisted allocation rows. When a portfolio has no holdings (legacy
    / partial data), we fall back to the frozen ingest-time allocation rows so we
    never show an empty current mix.

    Returns None when no portfolio / no holdings or allocation rows are present
    so the formatter can omit the "current mix" framing rather than show zeros.
    """
    portfolios = list(getattr(user, "portfolios", []) or [])
    if not portfolios:
        return None
    primary = next(
        (p for p in portfolios if getattr(p, "is_primary", False)),
        portfolios[0],
    )
    holdings = list(getattr(primary, "holdings", []) or [])
    allocations = list(getattr(primary, "allocations", []) or [])
    if not holdings and not allocations:
        return None

    totals = {"equity": 0.0, "debt": 0.0, "cash": 0.0, "others": 0.0}
    if holdings:
        mix = current_asset_class_mix(holdings)
        totals["equity"] += mix.get("Equity", 0.0)
        totals["debt"] += mix.get("Debt", 0.0)
        totals["others"] += mix.get("Others", 0.0)
        for a in allocations:
            if (getattr(a, "asset_class", None) or "").strip().lower() == "cash":
                totals["cash"] += float(getattr(a, "amount", 0.0) or 0.0)
    else:
        name_map = {
            "equity": "equity",
            "debt": "debt",
            "cash": "cash",
            "other": "others",
            "others": "others",
        }
        for a in allocations:
            key = name_map.get((getattr(a, "asset_class", None) or "").strip().lower())
            totals[key or "others"] += float(getattr(a, "amount", 0.0) or 0.0)

    grand = sum(totals.values())
    if grand <= 0:
        return None
    return {
        "pct": {k: round(v / grand * 100) for k, v in totals.items()},
        "inr": {k: v for k, v in totals.items()},
        "indian": {k: format_inr_indian(v) for k, v in totals.items()},
    }


def build_aa_facts_pack(
    output: GoalAllocationOutput,
    current_mix: dict[str, Any] | None = None,
    annual_income: float | None = None,
) -> dict[str, Any]:
    """Curated facts the LLM is allowed to cite.

    Keep small. Customer-tellable fields only — no internal subgroup keys,
    no fund/ISIN, no SEBI sub-categories.

    Money convention: every numeric ``*_inr`` field is paired with a sibling
    ``*_indian`` string pre-formatted in Indian notation. The chat formatter
    prompt instructs the LLM to copy ``*_indian`` verbatim and never compute
    its own lakh/crore conversion.

    Naming convention:
    - ``plan_target_*`` — what the AA engine recommends deploying. Built
      from ``asset_class_breakdown.recommended`` (the engine's post-adjustment
      allocation plan). The field name is deliberately distinct from the
      customer's actual holdings so any LLM sentence using it (e.g. "your
      plan target is 62% equity") cannot be mistaken for current holdings.
    - ``your_actual_holdings_today_*`` — the customer's TRUE current
      holdings, computed from ``PortfolioAllocation`` rows by
      ``compute_current_asset_class_mix``. Optional; absent when no
      portfolio data exists.
    """
    cs = output.client_summary
    acb = output.asset_class_breakdown
    recommended = acb.recommended

    by_horizon = []
    for split in recommended.per_bucket:
        bucket_total = split.equity + split.debt + split.others
        if bucket_total <= 0:
            continue
        by_horizon.append(
            {
                "horizon": split.bucket,
                "amount_inr": bucket_total,
                "amount_indian": format_inr_indian(bucket_total),
                "mix_pct": {
                    "equity": round(split.equity_pct),
                    "debt": round(split.debt_pct),
                    "others": round(split.others_pct),
                },
            }
        )

    goals = []
    for b in output.bucket_allocations:
        for g in b.goals:
            goals.append(
                {
                    "name": g.goal_name,
                    "amount_needed_inr": g.amount_needed,
                    "amount_needed_indian": format_inr_indian(g.amount_needed),
                    "horizon_months": g.time_to_goal_months,
                    "bucket": b.bucket,
                    "rationale": b.goal_rationales.get(g.goal_name),
                }
            )

    future = [
        {
            "horizon": fi.bucket,
            "funding_gap_inr": fi.future_investment_amount,
            "funding_gap_indian": format_inr_indian(fi.future_investment_amount),
            "purpose": fi.message,
        }
        for fi in output.future_investments_summary
    ]

    facts: dict[str, Any] = {
        "risk_score": cs.effective_risk_score,
        "risk_profile_category": category_for_effective_risk_score(
            float(cs.effective_risk_score)
        ),
        "age": cs.age,
        "total_corpus_inr": output.grand_total,
        "total_corpus_indian": format_inr_indian(output.grand_total),
        "emergency_fund_months": cs.emergency_fund_months,
        "monthly_household_expense_inr": cs.monthly_household_expense,
        "monthly_household_expense_indian": format_inr_indian(
            cs.monthly_household_expense
        ),
        # Income is fed to the allocation engine but dropped from client_summary;
        # the caller passes it back in so the narrator can reason about it (e.g.
        # an income-change question) instead of saying it's not on file. Null
        # when the profile has no income.
        "annual_income_inr": annual_income,
        "annual_income_indian": format_inr_indian(annual_income),
        "plan_target_pct": {
            "equity": round(recommended.equity_total_pct),
            "debt": round(recommended.debt_total_pct),
            "others": round(recommended.others_total_pct),
        },
        "plan_target_inr": {
            "equity": recommended.equity_total,
            "debt": recommended.debt_total,
            "others": recommended.others_total,
        },
        "plan_target_indian": {
            "equity": format_inr_indian(recommended.equity_total),
            "debt": format_inr_indian(recommended.debt_total),
            "others": format_inr_indian(recommended.others_total),
        },
        "by_horizon": by_horizon,
        "goals": goals,
        "future_investments": future,
    }
    if current_mix is not None:
        facts["your_actual_holdings_today_pct"] = current_mix["pct"]
        facts["your_actual_holdings_today_inr"] = current_mix["inr"]
        facts["your_actual_holdings_today_indian"] = current_mix["indian"]
    return facts


def _format_allocation_answer_long(
    output: GoalAllocationOutput, user_question: str
) -> str:
    """Longer wrapper used by the standalone allocation HTTP endpoint."""
    return f"Based on your question: {user_question}\n\n{build_fallback_brief(output, 'full')}"


# ---------------------------------------------------------------------------
# Blocking-message helpers
# ---------------------------------------------------------------------------

_MSG_MISSING_DOB = (
    "I need your date of birth to build a personalised allocation — it anchors "
    "your risk profile and time horizon. Head to your profile, add it, then "
    "ask me again."
)

_MSG_NO_API_KEY = (
    "The allocation engine is briefly unavailable — please try again in a few "
    "minutes. If it persists, ping us via the help option and we'll get you "
    "sorted right away."
)

_MSG_ENGINE_ERROR = (
    "We hit a quick snag while calculating your allocation — please try again "
    "in a moment. If it keeps happening, reach out via the help option and "
    "we'll take a look."
)

# No investable corpus on record (no CAMS statement and nothing self-reported), so
# there is nothing to size an allocation against. Generic on purpose — covers both
# the "haven't uploaded my statement yet" and the "investing elsewhere / just
# starting" customer, and points at the paths that work without holdings.
_MSG_NO_CORPUS = (
    "I don't have an investable amount for you yet, so there's nothing to build an "
    "allocation around. Upload your CAMS statement and I'll shape it around what "
    "you already hold — or start a monthly SIP and I'll design the mix around that."
)


# ---------------------------------------------------------------------------
# Core pipeline orchestration
# ---------------------------------------------------------------------------


async def compute_allocation_result(
    user,
    user_question: str,
    *,
    db: AsyncSession | None = None,
    persist_recommendation: bool = False,
    acting_user_id: uuid.UUID | None = None,
    chat_session_id: uuid.UUID | None = None,
    spine_mode: str | None = None,
    chat_ctx: TurnContext | None = None,
    gate_on_zero_corpus: bool = False,
) -> AllocationRunOutcome:
    """Build inputs, run the 7-step pipeline, optionally persist, and return.

    Tolerant of partial profiles — missing DOB / risk profile / tax profile /
    investment profile / goals all degrade to documented defaults in the
    input_builder. The engine still runs and produces an answer.

    ``gate_on_zero_corpus`` (opt-in) short-circuits a corpus-0 user with a
    redirect instead of an all-zero allocation. Only the STANDALONE user-facing
    callers set it — the rebalancing flow reuses this same computation and must
    NOT be gated (its corpus comes from holdings and a zero there is a data edge,
    not a "you have nothing to invest" signal), so the default is off.
    """
    trace_line("module: asset_allocation — building inputs")

    if chat_ctx is None:
        from app.domains.ai_engine.turn_context import (
            TurnContext,
        )  # lazy: avoids ai_bridge ↔ chat_core cycle at import time

        chat_ctx = TurnContext(
            user_ctx=user,
            user_question=user_question,
            conversation_history=[],
            client_context=None,
            session_id=chat_session_id or uuid.uuid4(),
            db=db,
            effective_user_id=acting_user_id or getattr(user, "id", uuid.uuid4()),
            last_agent_runs={},
            active_intent=None,
            chat_overrides=None,
        )

    try:
        alloc_input, build_debug = build_goal_allocation_input_for_user(chat_ctx)
    except Exception as exc:
        # Input builder is tolerant by design — anything reaching here is an
        # unexpected programming error, not a missing-field signal.
        logger.exception("asset_allocation input build failed: %s", exc)
        return AllocationRunOutcome(result=None, blocking_message=_MSG_ENGINE_ERROR)

    trace_line(
        f"effective_risk_score={alloc_input.effective_risk_score} "
        f"(willingness={alloc_input.risk_willingness}, capacity={alloc_input.risk_capacity_score})"
    )
    trace_line(
        f"allocation input: age={alloc_input.age}, corpus={alloc_input.total_corpus}, "
        f"goals={len(alloc_input.goals)}"
    )

    # No investable corpus (no CAMS, nothing self-reported): the engine scales
    # every amount and percentage by the corpus, so at 0 it renders an all-zero
    # allocation that reads as broken. Return a redirect instead of running the
    # pipeline — the no-CAMS cohort is pointed at CAMS upload or a SIP. Opt-in so
    # only the standalone allocation callers gate; rebalancing (which chains this
    # computation) is never short-circuited here.
    if gate_on_zero_corpus and alloc_input.total_corpus <= 0:
        trace_line("asset_allocation: zero corpus — returning no-corpus gate")
        return AllocationRunOutcome(result=None, blocking_message=_MSG_NO_CORPUS)

    api_key = get_settings().get_anthropic_asset_allocation_key()
    if not api_key:
        return AllocationRunOutcome(result=None, blocking_message=_MSG_NO_API_KEY)

    try:
        full_state, output = await asyncio.to_thread(_invoke_pipeline, alloc_input)
    except Exception as exc:
        logger.exception("asset_allocation pipeline failed: %s", exc)
        trace_line(f"asset_allocation ERROR: {exc!s}")
        return AllocationRunOutcome(result=None, blocking_message=_MSG_ENGINE_ERROR)

    for label, key in _STEP_MAP:
        trace_line(
            _summarize_step(label, key, full_state[key])
            if key in full_state
            else f"{label}: <missing in state>"
        )
    trace_line(f"GoalAllocationOutput grand_total={output.grand_total}")
    trace_line(f"input builder debug: {_short_json(build_debug, 600)}")

    reb_id: uuid.UUID | None = None
    snap_id: uuid.UUID | None = None
    aa_run_id: uuid.UUID | None = None
    if (
        db is not None
        and persist_recommendation
        and output is not None
        and acting_user_id is not None
    ):
        from app.domains.asset_allocation.services.allocation_recommendation_persist_service import (
            persist_goal_allocation_recommendation,
        )
        from app.domains.asset_allocation.services.aa_engine.persistence import (
            save_asset_allocation_from_engine_output,
        )

        from app.domains.practical_asset_allocation.services.paa_engine.input_builder import (
            load_human_override_for_user,
        )
        from practical_asset_allocation.human_override import apply_human_override
        from app.domains.profile.services.preference_tagging import (
            preference_id_for,
        )

        _prefs = load_human_override_for_user(user)
        if _prefs is not None:
            output, _ = apply_human_override(
                output, _prefs, alloc_input.multi_asset_composition
            )
        _applied_pref_id = preference_id_for(user, applied=_prefs is not None)

        reb_id, snap_id = await persist_goal_allocation_recommendation(
            db,
            acting_user_id,
            output,
            chat_session_id=chat_session_id,
            user_question=None,
            spine_mode=spine_mode,
        )
        trace_line(f"persisted: rebalancing_id={reb_id} snapshot_id={snap_id}")

        goal_id_map = {
            getattr(g, "goal_name", ""): getattr(g, "id", None)
            for g in getattr(user, "financial_goals", None) or []
        }
        try:
            aa_run_id = await save_asset_allocation_from_engine_output(
                db,
                user_id=acting_user_id,
                portfolio_id=None,
                chat_session_id=chat_session_id,
                pipeline_source="asset_allocation_pydantic",
                spine_mode=spine_mode,
                user_question=user_question,
                input_payload=alloc_input.model_dump(mode="json"),
                engine_result=output,
                financial_goal_ids_by_name=goal_id_map,
                saved_investment_preference_id=_applied_pref_id,
            )
            trace_line(f"persisted: asset_allocation_run_id={aa_run_id}")
        except Exception:
            logger.exception(
                "asset allocation persist failed; continuing without run id"
            )

    # Display the holdings-aware (PRACTICAL) allocation so the chat answer — and
    # the follow-up modes that rehydrate this snapshot — match the Invest /
    # rebalancing view. The IDEAL ``output`` above is still what we persist to
    # ``asset_allocation_runs`` (the base the rebalancing flow reuses), so
    # rebalancing never rebalances a practical-of-practical base. PracticalAllocation
    # Output is a superset of GoalAllocationOutput, so it flows through the facts
    # pack / formatter unchanged; we drop its extra ``corpus_breakdown`` when
    # snapshotting so the rehydrate (``GoalAllocationOutput.model_validate``) stays
    # valid. If the practical engine is unavailable, we fall back to showing ideal.
    display_output: Any = output
    try:
        from app.domains.practical_asset_allocation.services.paa_engine.service import (
            compute_practical_allocation_result,
        )

        practical_outcome = await compute_practical_allocation_result(
            user, user_question, chat_ctx=chat_ctx
        )
        if practical_outcome.result is not None:
            display_output = practical_outcome.result
        else:
            trace_line("practical allocation unavailable for display; showing ideal")
    except Exception:
        logger.exception("practical allocation for display failed; showing ideal")

    # Persist AgentRun row for follow-up reasoning. Does not replace
    # allocation_recommendation_persist; this captures structured I/O for chat.
    if db is not None and acting_user_id is not None and output is not None:
        try:
            await record_ai_module_run(
                db,
                user_id=acting_user_id,
                session_id=chat_session_id,
                module="asset_allocation",
                reason="full_pipeline_run",
                intent_detected=None,
                spine_mode=spine_mode,
                input_payload=alloc_input.model_dump(mode="json"),
                output_payload={
                    "allocation_result": display_output.model_dump(
                        mode="json", exclude={"corpus_breakdown"}
                    ),
                    "correlation_ids": {
                        "snapshot_id": str(snap_id) if snap_id else None,
                        "rebalancing_recommendation_id": str(reb_id)
                        if reb_id
                        else None,
                    },
                },
                emit_standard_log=False,
            )
        except Exception as exc:
            logger.warning("AgentRun persistence skipped (non-fatal): %s", exc)

    return AllocationRunOutcome(
        result=display_output,
        blocking_message=None,
        asset_allocation_run_id=aa_run_id,
        rebalancing_recommendation_id=reb_id,
        allocation_snapshot_id=snap_id,
    )


# ---------------------------------------------------------------------------
# Standalone HTTP entry point
# ---------------------------------------------------------------------------


async def generate_asset_allocation_response(
    user,
    user_question: str,
    *,
    db: AsyncSession | None = None,
    persist_recommendation: bool = False,
    acting_user_id: uuid.UUID | None = None,
) -> str:
    """Run allocation for standalone HTTP and return a full user-facing string."""
    outcome = await compute_allocation_result(
        user,
        user_question,
        db=db,
        persist_recommendation=persist_recommendation,
        acting_user_id=acting_user_id,
        spine_mode="api_asset_allocation",
        gate_on_zero_corpus=True,
    )
    if outcome.blocking_message:
        return outcome.blocking_message
    if outcome.result:
        return _format_allocation_answer_long(outcome.result, user_question)
    return (
        "I could not produce an allocation result.\n\n"
        "**Justification**\n"
        "- The allocation engine returned no structured output."
    )
