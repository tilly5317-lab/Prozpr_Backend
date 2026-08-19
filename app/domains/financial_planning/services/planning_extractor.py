"""Gateway to ``AI_Agents.response_extractor`` — the planning AI module.

Per the AI-module architectural rule, this is the ONLY place in the codebase
that touches the extractor agent. It does three things and makes no LLM call of
its own:

  1. **Builds the agent's input.** The field catalogue comes from the registry
     here, so the agent never imports ``app`` and never learns which table a
     field lives in — it reports a ``field_key`` and this layer resolves it.
  2. **Calls the agent.** One Haiku structured-output call, in
     ``AI_Agents/src/response_extractor/``.
  3. **Resolves the parts into values.** The agent reports (amount, magnitude,
     period) or a relative INSTRUCTION; every multiplication happens here, in
     ``operations``. That split is the safety story of the whole domain: asked
     to annualise "2.4 lakh a month" the model returned a figure a crore out at
     0.95 confidence, and a digit-count slip looks exactly as confident as a
     correct answer. A model cannot make that mistake about arithmetic it is
     never asked to do.

Sequence position: the brain runs ``intent_classifier`` first, and that agent
answers only "which service area is this?". This one answers "what is in it?".
Two agents on purpose — an extractor needs the whole field catalogue in its
prompt, and a market question should never pay for that.

What the agent is never given: a stored value. Relative changes are resolved
against the database on this side of the boundary, and the utterance and
history are redacted before they leave. See ``privacy``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field as dc_field
from typing import Any, Literal

from app.core.config import get_settings
from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.financial_planning.services import privacy
from app.domains.financial_planning.services.operations import (
    AmbiguousUnit,
    NoBaseline,
    Operation,
    RelativeChange,
    apply_relative,
    scale,
    to_stored_value,
)
from app.domains.profile.services.profile_field_registry import (
    FIELD_REGISTRY,
    FieldSpec,
)

ensure_ai_agents_path()

# Note: this is the ONLY permitted import of the extractor agent. Search for
# ``ResponseExtractor`` to confirm.
from response_extractor import (  # noqa: E402
    CapturableField,
    ConversationMessage,
    ExtractionInput,
    ResponseExtractor,
)

logger = logging.getLogger(__name__)

# Below this, PI reads the value back instead of writing it. Deliberately high:
# a confirmation costs one turn, a wrong income corrupts every projection built
# on it. A policy decision, so it lives here and not in the agent.
WRITE_CONFIDENCE_THRESHOLD = 0.8

_MAX_HISTORY = 6


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UnreadOperation:
    """Something we heard but could not turn into a value we would store."""

    field_key: str | None
    reason: Literal["ambiguous_unit", "no_baseline", "invalid", "low_confidence"]
    value: Any = None
    verbatim: str | None = None


@dataclass(frozen=True)
class PlanningRead:
    """One message, read and resolved."""

    kind: str
    operations: list[Operation] = dc_field(default_factory=list)
    unread: list[UnreadOperation] = dc_field(default_factory=list)
    unchanged: list[str] = dc_field(default_factory=list)
    clarification: str | None = None
    ambiguous_field_key: str | None = None
    failed: bool = False

    @property
    def is_confirm(self) -> bool:
        return self.kind == "confirm"

    @property
    def is_reject(self) -> bool:
        return self.kind == "reject"

    @property
    def is_defer(self) -> bool:
        return self.kind in ("defer", "refusal")

    @property
    def is_cancel(self) -> bool:
        return self.kind == "cancel"

    def by_target(self, target: str) -> list[Operation]:
        return [o for o in self.operations if o.target == target]

    @property
    def wants_projection(self) -> bool:
        return any(o.target == "plan" and o.verb == "project" for o in self.operations)


# ---------------------------------------------------------------------------
# Building the agent's input
# ---------------------------------------------------------------------------


def _catalogue() -> list[CapturableField]:
    """The registry, in the agent's vocabulary, in ask-priority order.

    Deliberately carries no table or column: the agent has no business knowing
    where a value is stored, and the write router owns that mapping.
    """
    return [
        CapturableField(
            key=fs.key,
            question=fs.question,
            input_kind=fs.input_kind,
            unit=fs.unit,
            options=list(fs.options),
            hint=fs.hint,
        )
        for fs in sorted(FIELD_REGISTRY.values(), key=lambda f: (f.priority, f.key))
    ]


def _history(history: list[dict[str, Any]] | None) -> list[ConversationMessage]:
    return [
        ConversationMessage(role=m["role"], content=m["content"])
        for m in privacy.redact_history(history, max_turns=_MAX_HISTORY)
    ]


# ---------------------------------------------------------------------------
# Resolution — the arithmetic the model never does
# ---------------------------------------------------------------------------


def _money_value(m: Any) -> float | None:
    if m is None or m.amount is None:
        return None
    magnitude = getattr(m.magnitude, "value", m.magnitude)
    return scale(m.amount, magnitude)


def _enum(value: Any) -> Any:
    """Agent enums arrive as members; the domain speaks their string values."""
    return getattr(value, "value", value)


def _goal_slots(raw: Any) -> dict[str, Any]:
    """The goal builder's slot vocabulary, with every figure already scaled."""
    if raw is None:
        return {}
    slots: dict[str, Any] = {}
    cost = _money_value(raw.cost)
    estimate = _money_value(raw.cost_estimate)
    if cost is not None:
        slots["cost_pv"] = cost
        slots["cost_source"] = "customer"
    elif estimate is not None:
        slots["cost_pv"] = estimate
        slots["cost_source"] = "assistant_estimate"

    for key, value in (
        ("goal_name", (raw.goal_name or "").strip() or None),
        ("goal_type", _enum(raw.goal_type)),
        ("years", raw.years),
        ("target_year", raw.target_year),
        ("target_age", raw.target_age),
        ("current_age", raw.current_age),
        ("inflation_pct", raw.inflation_pct),
        ("financed", raw.financed),
        ("down_payment", _money_value(raw.down_payment)),
        ("down_payment_pct", raw.down_payment_pct),
        ("interest_pct", raw.interest_pct),
        ("tenure_years", raw.tenure_years),
        ("sip_change", raw.sip_change),
    ):
        if value is not None:
            slots[key] = value
    return slots


def _resolve_profile_op(
    raw: Any,
    fs: FieldSpec,
    current_values: dict[str, Any],
) -> tuple[Operation | None, UnreadOperation | None]:
    """Turn one reported profile operation into a resolved one.

    Every conversion happens here, against the registry spec and — for a
    relative change — against the value already on file.
    """
    from app.domains.financial_planning.models import (
        SOURCE_CHAT_ANSWER,
        SOURCE_CHAT_RELATIVE,
    )

    verb = _enum(raw.verb)
    verbatim = privacy.verbatim_for_audit(raw.verbatim)
    confidence = max(0.0, min(1.0, float(raw.confidence)))

    if verb in ("clear", "read"):
        return (
            Operation(
                target="profile",
                verb=verb,
                field_key=fs.key,
                confidence=confidence,
                verbatim=verbatim,
                source=SOURCE_CHAT_ANSWER,
            ),
            None,
        )

    if verb == "adjust":
        if raw.change is None:
            return None, UnreadOperation(fs.key, "invalid", verbatim=verbatim)
        delta = raw.change.amount
        change = RelativeChange(
            direction=_enum(raw.change.direction),
            pct=raw.change.pct,
            amount=(delta.amount if delta else None),
            magnitude=(_enum(delta.magnitude) if delta else None),
            period=(_enum(delta.period) if delta else None),
        )
        try:
            value, basis = apply_relative(fs, current_values.get(fs.key), change)
        except NoBaseline:
            return None, UnreadOperation(fs.key, "no_baseline", verbatim=verbatim)
        except AmbiguousUnit:
            return None, UnreadOperation(fs.key, "ambiguous_unit", verbatim=verbatim)
        return (
            Operation(
                target="profile",
                verb="adjust",
                field_key=fs.key,
                value=value,
                confidence=confidence,
                verbatim=verbatim,
                basis=basis,
                source=SOURCE_CHAT_RELATIVE,
            ),
            None,
        )

    # verb == "set"
    try:
        value = to_stored_value(
            fs,
            amount=(raw.value.amount if raw.value else None),
            magnitude=(_enum(raw.value.magnitude) if raw.value else None),
            period=(_enum(raw.value.period) if raw.value else None),
            text_value=raw.text_value,
        )
    except AmbiguousUnit:
        return None, UnreadOperation(fs.key, "ambiguous_unit", verbatim=verbatim)
    if value is None or value == "":
        return None, UnreadOperation(fs.key, "invalid", verbatim=verbatim)
    if confidence < WRITE_CONFIDENCE_THRESHOLD:
        return None, UnreadOperation(
            fs.key, "low_confidence", value=value, verbatim=verbatim
        )
    return (
        Operation(
            target="profile",
            verb="set",
            field_key=fs.key,
            value=value,
            confidence=confidence,
            verbatim=verbatim,
            source=SOURCE_CHAT_ANSWER,
        ),
        None,
    )


def resolve(result: Any, current_values: dict[str, Any]) -> PlanningRead:
    """Turn the agent's raw report into resolved operations.

    Split out from ``read_message`` so the whole resolution layer — which is
    where every number is actually decided — is testable without an LLM.
    """
    from app.domains.financial_planning.models import SOURCE_CHAT_GOAL

    operations: list[Operation] = []
    unread: list[UnreadOperation] = []

    for raw in result.operations:
        target = _enum(raw.target)
        verb = _enum(raw.verb)

        if target == "plan":
            operations.append(
                Operation(
                    target="plan",
                    verb="project",
                    confidence=max(0.0, min(1.0, float(raw.confidence))),
                    verbatim=privacy.verbatim_for_audit(raw.verbatim),
                )
            )
            continue

        if target == "goal":
            operations.append(
                Operation(
                    target="goal",
                    verb=(
                        verb
                        if verb in ("create", "update", "delete", "read")
                        else "update"
                    ),
                    goal_ref=(raw.goal_ref or "").strip() or None,
                    slots=_goal_slots(raw.goal),
                    confidence=max(0.0, min(1.0, float(raw.confidence))),
                    verbatim=privacy.verbatim_for_audit(raw.verbatim),
                    source=SOURCE_CHAT_GOAL,
                )
            )
            continue

        key = (raw.field_key or "").strip()
        fs = FIELD_REGISTRY.get(key)
        if fs is None:
            # The registry is the authority; a key the model invented is dropped.
            logger.info("response extractor returned unknown field_key=%r; dropped", key)
            continue
        op, miss = _resolve_profile_op(raw, fs, current_values)
        if op is not None:
            operations.append(op)
        if miss is not None:
            unread.append(miss)

    clarification = (result.clarification or "").strip() or None
    ambiguous = next((u.field_key for u in unread if u.reason == "ambiguous_unit"), None)
    if clarification is None and ambiguous:
        fs = FIELD_REGISTRY.get(ambiguous)
        clarification = (
            "Is that per month or per year?"
            if fs is not None and fs.unit in ("inr_per_year", "inr_per_month")
            else f"Could you confirm the figure for {ambiguous.replace('_', ' ')}?"
        )

    return PlanningRead(
        kind=_enum(result.kind),
        operations=operations,
        unread=unread,
        unchanged=[k for k in (result.unchanged_fields or []) if k in FIELD_REGISTRY],
        clarification=clarification,
        ambiguous_field_key=ambiguous,
    )


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


async def read_message(
    *,
    utterance: str,
    current_values: dict[str, Any],
    asked_field_key: str | None = None,
    goal_names_on_file: list[str] | None = None,
    draft_summary: str | None = None,
    awaiting: str | None = None,
    history: list[dict[str, Any]] | None = None,
) -> PlanningRead:
    """Read ``utterance`` into resolved plan operations.

    ``current_values`` is the customer's stored figures, used ONLY to resolve
    relative changes. It never reaches the agent.

    Never raises: an agent failure returns ``PlanningRead(failed=True)`` so the
    caller can fall through rather than lose the turn.
    """
    payload = ExtractionInput(
        utterance=privacy.redact(utterance),
        capturable_fields=_catalogue(),
        asked_field_key=asked_field_key,
        awaiting=awaiting,
        goal_names_on_file=list(goal_names_on_file or []),
        draft_summary=draft_summary,
        history=_history(history),
    )

    try:
        agent = ResponseExtractor(
            api_key=get_settings().get_anthropic_financial_planning_key()
        )
        result = await agent.aextract(payload)
    except Exception as exc:
        logger.warning("response extractor failed (%s); caller will fall through", exc)
        return PlanningRead(kind="unrelated", failed=True)

    return resolve(result, current_values)


__all__ = [
    "PlanningRead",
    "UnreadOperation",
    "WRITE_CONFIDENCE_THRESHOLD",
    "read_message",
    "resolve",
]
