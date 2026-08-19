"""Pydantic I/O for the response extractor.

The agent runs immediately after ``intent_classifier``: the classifier says
WHICH service area the message belongs to, and this reads WHAT is in it. Those
are two jobs and they stay two agents — a classifier that also extracts needs
the whole field catalogue in its prompt on every single turn, including the
turns that are about the market.

Deliberately free of any ``app`` import, like every agent under ``src/``. The
caller passes the field catalogue in as ``capturable_fields`` rather than the
agent reaching for a registry it cannot see, which also means the agent has no
opinion about where a value is stored — it reports what was said, and the app
layer maps the field key to its table.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Target(str, Enum):
    """What an operation acts on."""

    PROFILE = "profile"  # one of the customer's stored figures/dates/preferences
    GOAL = "goal"  # something they want to buy or save for
    PLAN = "plan"  # the projection over everything


class Verb(str, Enum):
    """What is being done to it. Read as CRUD.

    New members also need the matching Literal in ``extractor.py`` — a drift
    test enforces it.
    """

    SET = "set"  # stated outright
    ADJUST = "adjust"  # stated as a change to what we hold ("up 20%")
    CLEAR = "clear"  # asked to be removed
    READ = "read"  # asked what we hold
    CREATE = "create"  # a goal not in their plan yet
    UPDATE = "update"  # a change to a goal that is
    DELETE = "delete"  # remove a goal
    PROJECT = "project"  # run the projection


class Magnitude(str, Enum):
    """The Indian scale word attached to a figure. The caller multiplies."""

    UNIT = "unit"
    THOUSAND = "thousand"
    LAKH = "lakh"
    CRORE = "crore"


class Period(str, Enum):
    """The period the customer framed a figure in. The caller converts."""

    PER_MONTH = "per_month"
    PER_YEAR = "per_year"
    NONE = "none"


class Direction(str, Enum):
    INCREASE = "increase"
    DECREASE = "decrease"


class MessageKind(str, Enum):
    """What the message IS, as a whole."""

    STATE = "state"  # stated something, answered, or asked for a change
    CORRECTION = "correction"  # changing something stated earlier
    CONFIRM = "confirm"  # agreeing to what we read back
    REJECT = "reject"  # what we read back is wrong
    REFUSAL = "refusal"  # declined or pushed back
    DEFER = "defer"  # later
    CANCEL = "cancel"  # drop the goal being built
    QUESTION = "question"  # asked about the plan, changing nothing
    UNRELATED = "unrelated"  # changed the subject


class GoalType(str, Enum):
    VEHICLE = "VEHICLE"
    HOME_PURCHASE = "HOME_PURCHASE"
    CHILD_EDUCATION = "CHILD_EDUCATION"
    WEDDING = "WEDDING"
    TRAVEL = "TRAVEL"
    RETIREMENT = "RETIREMENT"
    EMERGENCY_FUND = "EMERGENCY_FUND"
    WEALTH_CREATION = "WEALTH_CREATION"
    OTHER = "OTHER"


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------


class CapturableField(BaseModel):
    """One field the caller is willing to have read out of the message.

    The caller owns this list. The agent never decides what is capturable and
    never learns where a field is stored — it reports a ``field_key``, and the
    app layer resolves that key to a table.
    """

    key: str
    question: str = Field(description="How the advisor would ask for it, in their voice.")
    input_kind: str = Field(description="money | percent | integer | date | enum | text")
    unit: str = Field(
        default="none",
        description=(
            "The unit the value is STORED in — inr_per_year, inr_per_month, inr, "
            "percent, years, none. Told to the model so it reports the period the "
            "customer used; the model never converts into it."
        ),
    )
    options: list[str] = Field(
        default_factory=list,
        description="For an enum field: the allowed answers, verbatim as stored.",
    )
    hint: Optional[str] = Field(
        default=None, description="Extra steer when the phrasing is ambiguous."
    )


class ConversationMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ExtractionInput(BaseModel):
    """Everything the extractor is allowed to see.

    Note what is NOT here: any stored VALUE. The agent is told which fields
    exist and what units they are in, never what the customer earns or has
    saved. A relative change comes back as an instruction and the caller
    resolves it against the database — so a figure the agent was never given
    cannot leak out of it.
    """

    utterance: str = Field(description="The customer's message. Redacted by the caller.")
    capturable_fields: list[CapturableField] = Field(default_factory=list)
    asked_field_key: Optional[str] = Field(
        default=None, description="The field the advisor just asked about, if any."
    )
    awaiting: Optional[str] = Field(
        default=None,
        description=(
            "What was just asked, in plain words, when it was not a field — "
            "'whether the numbers are right', 'whether anything else changed'. "
            "Without it a bare 'no, everything's the same' has no antecedent."
        ),
    )
    goal_names_on_file: list[str] = Field(
        default_factory=list,
        description="The customer's own goal labels, so `goal_ref` can be matched.",
    )
    draft_summary: Optional[str] = Field(
        default=None, description="The goal being built right now, if there is one."
    )
    history: list[ConversationMessage] = Field(
        default_factory=list, description="Recent turns. Redacted and capped by the caller."
    )


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


class Money(BaseModel):
    """A figure in parts. Never a product — the caller multiplies."""

    amount: Optional[float] = None
    magnitude: Optional[Magnitude] = None
    period: Optional[Period] = None


class Change(BaseModel):
    """A change stated against whatever we already hold."""

    direction: Direction
    pct: Optional[float] = None
    amount: Optional[Money] = None


class GoalSlots(BaseModel):
    """What the message said about a goal."""

    goal_name: Optional[str] = None
    goal_type: Optional[GoalType] = None
    years: Optional[float] = None
    target_year: Optional[int] = None
    target_age: Optional[int] = None
    current_age: Optional[int] = None
    cost: Optional[Money] = None
    cost_estimate: Optional[Money] = None
    inflation_pct: Optional[float] = None
    financed: Optional[bool] = None
    down_payment: Optional[Money] = None
    down_payment_pct: Optional[float] = None
    interest_pct: Optional[float] = None
    tenure_years: Optional[float] = None
    sip_change: Optional[bool] = None


class ExtractedOperation(BaseModel):
    """One thing the customer wants done to their plan."""

    target: Target
    verb: Verb
    field_key: Optional[str] = None
    goal_ref: Optional[str] = None
    value: Optional[Money] = None
    text_value: Optional[str] = None
    change: Optional[Change] = None
    goal: Optional[GoalSlots] = None
    confidence: float = Field(ge=0.0, le=1.0)
    verbatim: Optional[str] = None


class ExtractionResult(BaseModel):
    """What the message contained."""

    kind: MessageKind
    operations: list[ExtractedOperation] = Field(default_factory=list)
    unchanged_fields: list[str] = Field(default_factory=list)
    clarification: Optional[str] = None


__all__ = [
    "CapturableField",
    "Change",
    "ConversationMessage",
    "Direction",
    "ExtractedOperation",
    "ExtractionInput",
    "ExtractionResult",
    "GoalSlots",
    "GoalType",
    "Magnitude",
    "MessageKind",
    "Money",
    "Period",
    "Target",
    "Verb",
]
