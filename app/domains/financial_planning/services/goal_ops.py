"""CRUD on the customer's goals — the goals half of the domain.

The old flow could only ever ADD a goal. A customer who wanted the car budget
raised, or the Europe trip dropped, had to leave the conversation and find the
goals screen — which is a strange thing to say to someone who just told you, in
words, exactly what they wanted changed.

So all four verbs live here, and all four write the same audit row
(``planning_write``, ``table_name='goals'``) carrying the goal WHOLE in
``previous_value``. That is what lets "actually, put that back" work after a
delete: the row is not reconstructed from a description, it is restored from
the object we removed.

What this module does NOT do is decide the numbers. Cost inflation, the loan
split and the EMI come from ``goals.services.goal_math`` via the builder; this
module maps a finished projection onto columns and back.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.financial_planning.models import SOURCE_CHAT_GOAL
from app.domains.financial_planning.services import planning_state as state
from app.domains.goals.models.enums import GoalPriority, GoalStatus
from app.domains.goals.models.financial_goal import FinancialGoal

logger = logging.getLogger(__name__)

# Columns a chat-built goal owns. Everything else on the row is left to the
# goals screen and the engine, and a restore puts back exactly this set.
_OWNED_COLUMNS: tuple[str, ...] = (
    "name",
    "goal_name",
    "status",
    "priority",
    "goal_date",
    "target_date",
    "goal_value_pv",
    "present_value_amount",
    "goal_value_fv",
    "inflation_rate",
    "inflation_annual",
    "is_downpayment_only",
    "upfront_amount",
    "target_pv",
    "downpayment_pct",
    "mortgage_tenure_years",
    "mortgage_interest_annual",
    "monthly_contribution",
    "notes",
)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


async def active_goals(db: AsyncSession, user_id: uuid.UUID) -> list[FinancialGoal]:
    rows = (
        await db.execute(
            select(FinancialGoal)
            .where(FinancialGoal.user_id == user_id)
            .order_by(FinancialGoal.goal_date.asc().nullslast())
        )
    ).scalars()
    return [
        g
        for g in rows
        if g.status is None or str(getattr(g.status, "value", g.status)).upper() == "ACTIVE"
    ]


def goal_names(goals: list[FinancialGoal]) -> list[str]:
    return [g.display_name for g in goals if g.display_name]


def summarise(goal: FinancialGoal) -> dict[str, Any]:
    """One goal, in the shape the reply reads it back in."""
    from app.domains.ai_engine.common import format_inr_indian

    target = goal.goal_date or goal.target_date
    out: dict[str, Any] = {
        "goal": goal.display_name,
        "target_date": target.isoformat() if target else None,
    }
    if target:
        out["years_away"] = round((target - date.today()).days / 365.25, 1)
    if goal.goal_value_fv is not None:
        out["cost_at_that_time"] = format_inr_indian(float(goal.goal_value_fv))
    if goal.goal_value_pv is not None:
        out["plan_saves_for"] = format_inr_indian(float(goal.goal_value_pv))
    if goal.is_downpayment_only:
        out["financed"] = True
        if goal.mortgage_interest_annual is not None:
            out["interest_pct"] = float(goal.mortgage_interest_annual)
        if goal.mortgage_tenure_years is not None:
            out["loan_years"] = int(goal.mortgage_tenure_years)
    if goal.monthly_contribution is not None:
        out["monthly_contribution"] = format_inr_indian(float(goal.monthly_contribution))
    return out


def resolve_ref(goals: list[FinancialGoal], ref: str | None) -> FinancialGoal | None:
    """Match what the customer called a goal to a row.

    Customers do not have ids; they say "the car" and "that Europe trip". The
    match is deliberately narrow — exact, then containment either way, and an
    AMBIGUOUS match returns ``None`` so the caller asks which one rather than
    editing a goal at random.
    """
    if not ref:
        return goals[0] if len(goals) == 1 else None
    needle = ref.strip().casefold()
    if not needle:
        return None

    exact = [g for g in goals if g.display_name.casefold() == needle]
    if len(exact) == 1:
        return exact[0]

    partial = [
        g
        for g in goals
        if needle in g.display_name.casefold() or g.display_name.casefold() in needle
    ]
    if len(partial) == 1:
        return partial[0]

    # Word overlap, for "the car goal" against "Thar 4x4".
    words = {w for w in needle.split() if len(w) > 3}
    if words:
        overlap = [
            g
            for g in goals
            if words & {w for w in g.display_name.casefold().split() if len(w) > 3}
        ]
        if len(overlap) == 1:
            return overlap[0]
    return None


# ---------------------------------------------------------------------------
# Snapshot / restore — what makes a delete undoable
# ---------------------------------------------------------------------------


def to_json(goal: FinancialGoal) -> dict[str, Any]:
    """The goal as a plain object, complete enough to put back."""
    out: dict[str, Any] = {"id": str(goal.id)}
    for column in _OWNED_COLUMNS:
        value = getattr(goal, column, None)
        out[column] = state.jsonable(getattr(value, "value", value))
    return out


async def restore(
    db: AsyncSession, user_id: uuid.UUID, snapshot: dict[str, Any]
) -> FinancialGoal:
    """Put a deleted goal back, or roll an edit back to its previous shape."""
    goal_id = snapshot.get("id")
    existing = None
    if goal_id:
        existing = (
            await db.execute(
                select(FinancialGoal)
                .where(FinancialGoal.id == uuid.UUID(str(goal_id)))
                .where(FinancialGoal.user_id == user_id)
            )
        ).scalar_one_or_none()

    goal = existing or FinancialGoal(
        id=uuid.UUID(str(goal_id)) if goal_id else uuid.uuid4(), user_id=user_id
    )
    for column in _OWNED_COLUMNS:
        if column not in snapshot:
            continue
        _set_column(goal, column, snapshot[column])
    if existing is None:
        db.add(goal)
    await db.flush()
    return goal


def _set_column(goal: FinancialGoal, column: str, value: Any) -> None:
    """Put a JSON value back into a typed column."""
    if value is None:
        setattr(goal, column, None)
        return
    if column in ("goal_date", "target_date") and isinstance(value, str):
        setattr(goal, column, date.fromisoformat(value))
        return
    if column == "status":
        setattr(goal, column, GoalStatus(str(value)))
        return
    if column == "priority":
        setattr(goal, column, GoalPriority(str(value)))
        return
    setattr(goal, column, value)


# ---------------------------------------------------------------------------
# Create / update / delete
# ---------------------------------------------------------------------------


def build_goal(
    user_id: uuid.UUID, slots: dict[str, Any], proj: dict[str, Any]
) -> FinancialGoal:
    """Map a confirmed projection onto the canonical goal row.

    ``goal_value_pv`` is what the ENGINE inflates, so for a financed purchase it
    carries the down payment with ``inflation_rate=0`` — the customer named a
    nominal amount they will hand over, not a sum in today's purchasing power,
    and re-inflating it would invent a requirement they never stated.
    """
    goal = FinancialGoal(user_id=user_id)
    apply_projection(goal, slots, proj)
    return goal


def apply_projection(
    goal: FinancialGoal, slots: dict[str, Any], proj: dict[str, Any]
) -> None:
    """Write a projection onto a goal row — the same mapping for create and update."""
    financed = bool(proj.get("financed"))
    name = (slots.get("goal_name") or goal.display_name or "Goal").strip()[:100]
    target = date.fromisoformat(proj["target_date"])

    if financed:
        corpus_pv = float(proj["down_payment"])
        inflation = 0.0
    else:
        corpus_pv = float(proj["cost_pv"])
        inflation = float(proj["inflation_pct"])

    goal.name = name
    goal.goal_name = name
    goal.status = GoalStatus.ACTIVE
    goal.priority = goal.priority or GoalPriority.HIGH
    # goal_type is left NULL, exactly as POST /goals does: the column is a
    # `cashflow_goal_type_enum` (retirement / property / child_* / custom), not
    # the richer taxonomy the conversation uses, and the engine falls through to
    # `custom` for NULL — which is the correct treatment for a down-payment
    # goal. The conversational category goes in the notes instead, so
    # chat-created and API-created goals look identical.
    goal.goal_date = target
    goal.target_date = target
    goal.goal_value_pv = corpus_pv
    goal.present_value_amount = corpus_pv
    goal.goal_value_fv = float(proj["cost_fv"])
    goal.inflation_rate = inflation
    goal.inflation_annual = inflation
    goal.is_downpayment_only = financed
    goal.upfront_amount = float(proj["down_payment"]) if financed else None
    # The loan block in the same shape the goals screen writes it, so a
    # chat-built goal opens there showing its loan instead of a blank section.
    # Expressed at PURCHASE time (total = inflated cost), because that is the
    # money the customer actually named: "fifty lakh down" is fifty lakh handed
    # over on the day, not fifty lakh of today's value.
    goal.target_pv = float(proj["cost_fv"]) if financed else None
    # Fraction, not percent — the column is checked against 0..1 while
    # inflation_rate beside it is checked against 0..50.
    goal.downpayment_pct = (
        max(0.0, min(1.0, float(proj["down_payment"]) / float(proj["cost_fv"])))
        if financed and float(proj.get("cost_fv") or 0) > 0
        else None
    )
    goal.mortgage_tenure_years = (
        int(proj["tenure_years"]) if financed and proj.get("tenure_years") else None
    )
    goal.mortgage_interest_annual = (
        float(proj["interest_pct"]) if financed and proj.get("interest_pct") else None
    )
    goal.notes = provenance_note(slots, proj)


def provenance_note(slots: dict[str, Any], proj: dict[str, Any]) -> str:
    """A one-line audit trail on the goal itself, so someone reading the goals
    list six weeks later can see where these numbers came from."""
    bits = [f"Set from chat on {date.today().isoformat()}."]
    if slots.get("goal_type"):
        bits.append(f"Category: {slots['goal_type']}.")
    if slots.get("cost_source") == "assistant_estimate":
        bits.append("Today's price was our estimate, accepted by the customer.")
    if proj.get("financed"):
        bits.append(
            f"Financed: {proj['interest_pct']:g}% over {proj['tenure_years']:g}y; "
            f"EMI approx {proj['monthly_emi']:.0f}/month. Plan saves for the "
            "down payment only."
        )
    return " ".join(bits)[:2000]


async def record_goal_write(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID | None,
    goal: FinancialGoal,
    previous: dict[str, Any] | None,
    new: dict[str, Any] | None,
    verbatim: str | None = None,
):
    """Audit a goal create / update / delete. ``previous=None`` means it is new;
    ``new=None`` means it was removed. The row is what ``downstream`` reads to
    decide the plan cache has to be retired — so a goal change that is not
    recorded here is a goal change the projection will not notice."""
    return await state.record_write(
        db,
        user_id=user_id,
        session_id=session_id,
        ask_id=None,
        field_key=str(goal.id),
        table_name="goals",
        column_name="*",
        previous=previous,
        value=new,
        source=SOURCE_CHAT_GOAL,
        verbatim=verbatim,
    )


async def delete_goal(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID | None,
    goal: FinancialGoal,
    verbatim: str | None = None,
) -> tuple[dict[str, Any], Any]:
    """Remove a goal, keeping it whole on the audit row so it can be put back."""
    snapshot = to_json(goal)
    write = await record_goal_write(
        db,
        user_id=user_id,
        session_id=session_id,
        goal=goal,
        previous=snapshot,
        new=None,
        verbatim=verbatim,
    )
    await db.delete(goal)
    await db.flush()
    logger.info("financial_planning deleted goal=%s for user=%s", snapshot["id"], user_id)
    return snapshot, write


__all__ = [
    "active_goals",
    "apply_projection",
    "build_goal",
    "delete_goal",
    "goal_names",
    "provenance_note",
    "record_goal_write",
    "resolve_ref",
    "restore",
    "summarise",
    "to_json",
]
