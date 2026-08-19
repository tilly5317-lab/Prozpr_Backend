"""What has to be re-run when a plan input actually changes — and nothing more.

A write in this domain is never just a write. Changing an income moves the
cashflow projection; changing how someone would react to a 20% fall moves their
risk score; adding a goal moves both. Chasing all of that on every turn is the
easy mistake and an expensive one: the risk re-score and the plan re-run are
each a real amount of work, and firing them after a turn that changed nothing
is pure cost for an identical answer.

So the rule here is table-driven and strictly one-way: **an effect runs only
when a column it actually depends on was actually written on this turn.** The
audit rows are the input — the same rows undo reads — so an effect can never
fire for a change that was staged and never confirmed, or for one the customer
backed out of.

Each effect declares the columns it depends on, in the registry's own
vocabulary, so adding a field to the registry cannot silently start or stop
triggering a downstream job without someone editing the dependency list below.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.profile.services.profile_field_registry import FIELD_REGISTRY

logger = logging.getLogger(__name__)
# The decision trail goes on the same logger as every other AI module's
# telemetry, so one grep covers a whole turn. Diagnostics stay on `logger`.
trail = logging.getLogger("ailax.ai_bridge")


@dataclass(frozen=True)
class Change:
    """One thing this turn wrote. Mirrors the audit row it came from."""

    table: str
    column: str
    field_key: str | None = None
    # Human-readable name of the thing that changed — the field key, or a goal's
    # own name. Only used for logging, so an operator reading the line does not
    # have to look a UUID up.
    label: str | None = None

    def describe(self) -> str:
        """``personal_finance_profiles.annual_income`` / ``goals[Thar 4x4]``."""
        if self.table == "goals":
            return "goals[" + (self.label or self.field_key or "?") + "]"
        return f"{self.table}.{self.column}"


# The profile fields the cashflow / goal-planning engine reads. Taken from
# ``cashflow.services.goal_planning_engine.input_builder`` — if that builder
# starts reading another column, add it here, or a plan will be served from a
# cache that predates the input it was built on.
_PLAN_INPUT_FIELDS: frozenset[str] = frozenset(
    {
        "annual_income",
        "monthly_household_expense",
        "financial_assets",
        "equity_shares",
        "financial_liabilities_excl_mortgage",
        "starting_monthly_investment",
        "date_of_birth",
        "retirement_age",
        "target_corpus",
        "income_tax_rate",
    }
)

# Fields the effective-risk score is built from. Sourced from the registry's own
# ``risk_input`` flag rather than restated, so the two cannot drift.
_RISK_INPUT_FIELDS: frozenset[str] = frozenset(
    k for k, fs in FIELD_REGISTRY.items() if fs.risk_input
)


def _plan_matches(changes: Iterable[Change]) -> list[Change]:
    return [
        c for c in changes if c.table == "goals" or c.field_key in _PLAN_INPUT_FIELDS
    ]


def _risk_matches(changes: Iterable[Change]) -> list[Change]:
    return [c for c in changes if c.field_key in _RISK_INPUT_FIELDS]


async def _rescore_risk(db: AsyncSession, user_id: uuid.UUID) -> None:
    from app.domains.profile.services._effective_risk import (
        maybe_recalculate_effective_risk,
    )

    await maybe_recalculate_effective_risk(
        db, user_id, trigger_reason="risk_profile_update"
    )


async def _retire_plan_cache(db: AsyncSession, user_id: uuid.UUID) -> None:
    from app.domains.cashflow.services.cashflow_persist_service import mark_stale

    # commit=False: the chat router owns the transaction, so this has to roll
    # back with the write that made it necessary.
    await mark_stale(db, user_id, commit=False)


@dataclass(frozen=True)
class Effect:
    name: str
    # Returns the changes this effect depends on — not a bool, because the
    # matching changes ARE the audit trail for why it ran.
    matches: Callable[[Iterable[Change]], list[Change]]
    run: Callable[[AsyncSession, uuid.UUID], Awaitable[None]]
    why: str
    # What it depends on, in one phrase, for the line an operator reads when it
    # did NOT run.
    depends_on: str


EFFECTS: tuple[Effect, ...] = (
    Effect(
        name="effective_risk",
        matches=_risk_matches,
        run=_rescore_risk,
        why="a risk input moved, so the stored score no longer reflects them",
        depends_on="the registry fields flagged risk_input",
    ),
    Effect(
        name="cashflow_plan_cache",
        matches=_plan_matches,
        run=_retire_plan_cache,
        why="a plan input or a goal moved, so the cached projection predates it",
        depends_on="the goals table and the columns the cashflow input builder reads",
    ),
)


@dataclass(frozen=True)
class EffectOutcome:
    """What happened to one effect on this turn, and why.

    ``triggered_by`` is the point of the whole record: it names the exact
    columns that caused the work, so "why did my plan get recomputed?" has an
    answer that is not a guess.
    """

    name: str
    ran: bool
    triggered_by: tuple[str, ...] = ()
    skipped_reason: str | None = None
    error: str | None = None

    def as_line(self) -> str:
        if self.error:
            return f"{self.name}=FAILED({self.error})"
        if self.ran:
            return self.name + "<-" + "+".join(self.triggered_by)
        return f"{self.name}=skipped"


@dataclass(frozen=True)
class FireReport:
    """Every effect considered on this turn — the ones that ran and the ones
    that did not. Both halves matter: the skipped ones are the evidence that a
    turn which changed nothing relevant did no work."""

    outcomes: tuple[EffectOutcome, ...] = ()

    @property
    def fired(self) -> list[str]:
        return [o.name for o in self.outcomes if o.ran]

    @property
    def skipped(self) -> list[str]:
        return [o.name for o in self.outcomes if not o.ran and not o.error]

    def as_line(self) -> str:
        return " ".join(o.as_line() for o in self.outcomes) or "none"

    def as_payload(self) -> list[dict[str, Any]]:
        return [
            {
                "effect": o.name,
                "ran": o.ran,
                "triggered_by": list(o.triggered_by),
                "skipped_reason": o.skipped_reason,
                "error": o.error,
            }
            for o in self.outcomes
        ]


async def fire(
    db: AsyncSession,
    user_id: uuid.UUID,
    changes: list[Change],
) -> FireReport:
    """Run the effects whose inputs this turn actually changed.

    Returns a report covering EVERY effect, run or not, with the columns that
    triggered each one. Failures are logged and swallowed: a customer's income
    is correctly saved even if the re-score behind it fell over, and telling
    them otherwise would be a lie about the thing they asked for.
    """
    outcomes: list[EffectOutcome] = []
    for effect in EFFECTS:
        matched = list(effect.matches(changes)) if changes else []
        if not matched:
            outcomes.append(
                EffectOutcome(
                    name=effect.name,
                    ran=False,
                    skipped_reason=(
                        "nothing was written this turn"
                        if not changes
                        else f"nothing written touched {effect.depends_on}"
                    ),
                )
            )
            continue
        triggered_by = tuple(dict.fromkeys(c.describe() for c in matched))
        try:
            await effect.run(db, user_id)
            outcomes.append(
                EffectOutcome(name=effect.name, ran=True, triggered_by=triggered_by)
            )
        except Exception as exc:
            logger.exception(
                "AILAX_FP_EFFECT effect=%s status=failed user_id=%s triggered_by=%s "
                "-- the write itself stands",
                effect.name,
                user_id,
                ",".join(triggered_by),
            )
            outcomes.append(
                EffectOutcome(
                    name=effect.name,
                    ran=False,
                    triggered_by=triggered_by,
                    error=type(exc).__name__,
                )
            )

    report = FireReport(outcomes=tuple(outcomes))
    trail.info(
        "AILAX_FP_EFFECTS user_id=%s changes=%s | %s",
        user_id,
        ",".join(c.describe() for c in changes) or "none",
        report.as_line(),
    )
    return report


def changes_from_writes(rows: Iterable[Any]) -> list[Change]:
    """Audit rows -> the change list. The one adapter, so nothing else has to
    know the row shape."""
    out: list[Change] = []
    for r in rows:
        is_goal = r.table_name == "goals"
        out.append(
            Change(
                table=r.table_name,
                column=r.column_name,
                # A goal id is not a registry key and must never be matched as
                # one, so it is deliberately dropped here.
                field_key=(None if is_goal else r.field_key),
                label=(_goal_label(r) if is_goal else r.field_key),
            )
        )
    return out


def _goal_label(row: Any) -> str | None:
    """A goal's own name, from whichever side of the write still has it."""
    for side in (row.new_value, row.previous_value):
        if isinstance(side, dict):
            name = side.get("name") or side.get("goal_name")
            if name:
                return str(name)
    return None


def plan_inputs_changed(changes: Iterable[Change]) -> bool:
    """Should the projection be re-run and reported on this turn?

    The same predicate the cache invalidation uses, exposed so the module can
    answer "does this change alter their verdict?" without duplicating the list.
    """
    return bool(_plan_matches(changes))


__all__ = [
    "Change",
    "EFFECTS",
    "Effect",
    "EffectOutcome",
    "FireReport",
    "changes_from_writes",
    "fire",
    "plan_inputs_changed",
]
