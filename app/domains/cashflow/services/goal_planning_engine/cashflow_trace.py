"""Structured tracing for cashflow / goal-planning engine runs.

A run is logged in four stages so it can be reconstructed end-to-end from the
logs: **trigger → inputs → processing → output**. This is the "chatbrain log"
the goal-planning module emits whenever the cashflow engine runs — it sits
alongside ``ChatBrain``'s own ``[AILAX_TRACE]`` lines (same prefix), so a single
grep for ``AILAX_TRACE`` shows the whole turn including the cashflow internals.

Each helper emits BOTH:
  * a greppable ``[AILAX_TRACE] CASHFLOW/<stage> ...`` line (stdout, via
    ``ai_engine.common.trace_line`` — the convention the chat brain uses), and
  * a ``logging`` record on the ``ailax.cashflow`` logger (so the same content
    is captured by whatever logging handlers are configured).

The engine runs through TWO entry paths and both call these helpers, tagged via
``path`` so you can tell them apart:
  * ``path="chat"``  — ChatBrain → flow_goal_planning → goal_planning_chat
                       → service.compute_goal_planning_snapshot
  * ``path="rest"``  — GET /cashflow/latest · POST /cashflow/compute
                       → cashflow_compute_service.run_cashflow_projection_for_user

``log_inputs`` lives in the single shared chokepoint
(``input_builder.build_goal_planning_input_for_user``) so it covers both paths.
"""

from __future__ import annotations

import logging
from typing import Any

from app.domains.ai_engine.common import format_inr_indian, trace_line

logger = logging.getLogger("ailax.cashflow")

_TAG = "CASHFLOW"


def _inr(v: Any) -> str:
    try:
        return format_inr_indian(float(v)) or "₹0"
    except (TypeError, ValueError):
        return "—"


def _emit(stage: str, msg: str) -> None:
    """Mirror one line to the AILAX trace stream and the cashflow logger."""
    line = f"{_TAG}/{stage} {msg}"
    trace_line(line)
    logger.info(line)


def _uid(user_id: Any) -> str:
    return str(user_id) if user_id is not None else "?"


def _sid(session_id: Any) -> str:
    return str(session_id) if session_id is not None else "-"


def log_trigger(
    *,
    path: str,
    user_id: Any,
    session_id: Any = None,
    reason: str | None = None,
) -> None:
    """A cashflow run was triggered (entry point reached, before building input)."""
    extra = f" reason={reason}" if reason else ""
    _emit("trigger", f"path={path} user={_uid(user_id)} session={_sid(session_id)}{extra}")


def log_inputs(
    *,
    user_id: Any,
    gp_input: Any,
    debug: dict[str, Any] | None = None,
    session_id: Any = None,
) -> None:
    """Every input the engine will run on — profile scalars, retirement, goals.

    Logged from the shared input-builder chokepoint, so it fires for both the
    chat and REST paths. Values are the user's real numbers (the readiness gate
    upstream guarantees nothing is defaulted/placeholder).
    """
    debug = debug or {}
    p = gp_input.profile
    r = gp_input.retirement

    _emit(
        "inputs.profile",
        f"user={_uid(user_id)} session={_sid(session_id)} "
        f"annual_income={_inr(p.annual_income)} "
        f"effective_tax_rate={p.effective_tax_rate:.4f} "
        f"monthly_household_expense={_inr(p.monthly_household_expense)} "
        f"financial_assets={_inr(p.financial_assets)} "
        f"financial_liabilities_excl_mortgage={_inr(p.financial_liabilities_excl_mortgage)} "
        f"starting_monthly_investment={_inr(p.starting_monthly_investment)}",
    )

    _emit(
        "inputs.retirement",
        f"user={_uid(user_id)} date_of_birth={r.date_of_birth} "
        f"retirement_age={r.retirement_age} (from user, not assumed) "
        f"assumed_lifespan_years={r.assumed_lifespan_years} (from user, not assumed) "
        f"retirement_corpus_pv_today_override="
        f"{_inr(r.retirement_corpus_pv_today_override) if r.retirement_corpus_pv_today_override else 'none'}",
    )

    goals = list(gp_input.custom_goals or [])
    _emit(
        "inputs.goals",
        f"user={_uid(user_id)} custom_goals={len(goals)} "
        f"current_properties={len(gp_input.current_properties or [])}",
    )
    for g in goals:
        gt = g.goal_type.value if hasattr(g.goal_type, "value") else str(g.goal_type)
        _emit(
            "inputs.goal",
            f"user={_uid(user_id)} name={g.name!r} type={gt} "
            f"target_date={g.goal_date} value_pv={_inr(g.goal_value_pv)} "
            f"inflation_override={g.inflation_rate_override}",
        )

    defaults_applied = debug.get("defaults_applied") or []
    validation_issues = debug.get("validation_issues") or []
    _emit(
        "inputs.meta",
        f"user={_uid(user_id)} has_pfp={debug.get('has_personal_finance_profile')} "
        f"has_investment_profile={debug.get('has_investment_profile')} "
        f"active_goal_count={debug.get('active_goal_count')} "
        f"structural_defaults={defaults_applied}",
    )
    for issue in validation_issues:
        _emit("inputs.validation", f"user={_uid(user_id)} {issue}")


def log_output(
    *,
    path: str,
    user_id: Any,
    output: Any,
    plan_run_id: Any = None,
    session_id: Any = None,
) -> None:
    """The computed plan — horizon, retirement, headline, fund flow, warnings."""
    r = output.retirement
    h = output.headline
    ff = output.fund_flow_summary

    # Horizon: the projection runs to the LATER of retirement and the last goal.
    _emit(
        "processing.horizon",
        f"user={_uid(user_id)} retirement_date={r.retirement_date} "
        f"last_goal_date={h.last_goal_date} "
        f"projection_end_fy={h.last_fy_end_date} years_to_last_goal={h.years_to_last_goal} "
        f"-> horizon = max(retirement_date, last_goal_date) "
        f"post_retirement_years={r.post_retirement_years}",
    )

    _emit(
        "output.headline",
        f"user={_uid(user_id)} session={_sid(session_id)} path={path} "
        f"is_feasible={h.is_feasible} number_of_goals={h.number_of_goals} "
        f"corpus_today={_inr(h.corpus_today)} "
        f"total_corpus_required_today={_inr(h.total_corpus_required_today)} "
        f"surplus_or_shortfall_today={_inr(h.surplus_or_shortfall_today)} "
        f"corpus_closing={_inr(h.corpus_closing)} "
        f"total_shortfall_fv={_inr(h.total_shortfall_fv)} "
        f"total_funded_amount={_inr(h.total_funded_amount)}",
    )

    _emit(
        "output.fund_flow",
        f"user={_uid(user_id)} corpus_opening={_inr(ff.corpus_opening)} "
        f"+investments={_inr(ff.total_investments)} +roi={_inr(ff.total_roi)} "
        f"-goals_paid={_inr(ff.total_goals_paid)} corpus_closing={_inr(ff.corpus_closing)}",
    )

    monthly = getattr(output, "monthly_cashflow", None) or []
    _emit(
        "output.rows",
        f"user={_uid(user_id)} annual_rows={len(output.annual_cashflow or [])} "
        f"monthly_rows={len(monthly)} plan_run_id={plan_run_id} "
        f"engine_version={output.engine_version}",
    )

    for w in (output.warnings or []):
        _emit("output.warning", f"user={_uid(user_id)} {w}")


def log_skipped(
    *,
    path: str,
    user_id: Any,
    error: str,
    session_id: Any = None,
) -> None:
    """The run was refused before computing — usually the readiness gate."""
    _emit(
        "skipped",
        f"path={path} user={_uid(user_id)} session={_sid(session_id)} reason={error}",
    )
