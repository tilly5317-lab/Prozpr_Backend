"""The extractor pipeline: one message -> typed operations.

Runs immediately after ``intent_classifier`` and does the job the classifier
deliberately does not: reading WHAT is in the message. Keeping them apart is
what lets the classifier stay a small, cached, single-purpose call — an
extractor needs the whole field catalogue in its prompt, and a market question
should never pay for that.

Same mechanics as the classifier next door: LangChain + Claude Haiku with
structured output, prompt caching on the static system block, and
``temperature=0`` pinned as a literal.
"""

from __future__ import annotations

import os
import re
from typing import Any, Literal, Optional

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, field_validator

from .models import ExtractionInput, ExtractionResult
from .prompts import SYSTEM_PROMPT, build_user_block

load_dotenv()

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_MAX_TOKENS = 1400


# NOTE: keep these Literals in sync with the enums in ``models.py``. They are
# hard-coded rather than derived because ``Literal[*tuple(...)]`` unpacking is
# not available on the Python this package supports. Constraining them here
# makes the Anthropic tool schema enforce each vocabulary at the API level, so
# the model physically cannot emit an unknown verb or target. A drift test
# (``app/domains/financial_planning/tests/test_response_extractor_schema.py``)
# fails loudly if the two get out of sync.
_TargetLiteral = Literal["profile", "goal", "plan"]
_VerbLiteral = Literal[
    "set", "adjust", "clear", "read", "create", "update", "delete", "project"
]
_MagnitudeLiteral = Literal["unit", "thousand", "lakh", "crore"]
_PeriodLiteral = Literal["per_month", "per_year", "none"]
_DirectionLiteral = Literal["increase", "decrease"]
_MessageKindLiteral = Literal[
    "state",
    "correction",
    "confirm",
    "reject",
    "refusal",
    "defer",
    "cancel",
    "question",
    "unrelated",
]
_GoalTypeLiteral = Literal[
    "VEHICLE",
    "HOME_PURCHASE",
    "CHILD_EDUCATION",
    "WEDDING",
    "TRAVEL",
    "RETIREMENT",
    "EMERGENCY_FUND",
    "WEALTH_CREATION",
    "OTHER",
]

# Anthropic's tool-call serializer occasionally leaks a closing tag into a
# value (observed in prod on the classifier: ``"true</is_follow_up>"``).
# Anything from the first '<' onward is discarded.
_TAG_NOISE_RE = re.compile(r"<[^>]*>.*$", re.DOTALL)


def _scrub_tag_noise(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return _TAG_NOISE_RE.sub("", value).strip()


# ---------------------------------------------------------------------------
# The tool schema the model fills in
# ---------------------------------------------------------------------------


class _Money(BaseModel):
    amount: Optional[float] = Field(
        default=None,
        description=(
            "The bare figure exactly as the customer said it, with NO scaling. "
            "'two point four lakh' -> 2.4. '90 thousand' -> 90. '1.5 cr' -> 1.5. "
            "'Rs 28,80,000' -> 2880000. 'the 30% slab' -> 30."
        ),
    )
    magnitude: Optional[_MagnitudeLiteral] = Field(
        default=None,
        description=(
            "The scale word attached to `amount`. 'lakh'/'L'/'lac' -> lakh. "
            "'crore'/'cr' -> crore. 'k'/'thousand' -> thousand. A plain figure "
            "with no scale word -> unit."
        ),
    )
    period: Optional[_PeriodLiteral] = Field(
        default=None,
        description=(
            "Whether they framed the figure per month, per year, or as a plain "
            "total. Report what they SAID; never infer from habit."
        ),
    )

    @field_validator("magnitude", "period", mode="before")
    @classmethod
    def _scrub(cls, v: Any) -> Any:
        return _scrub_tag_noise(v)


class _Change(BaseModel):
    direction: _DirectionLiteral = Field(description="Which way the figure moved.")
    pct: Optional[float] = Field(
        default=None,
        description=(
            "The percentage, when they gave one: 'up 20%' -> 20, 'a 10 percent "
            "cut' -> 10. Always positive; `direction` carries the sign. Null if "
            "they gave an amount instead."
        ),
    )
    amount: Optional[_Money] = Field(
        default=None,
        description=(
            "The amount, when they gave one instead of a percentage: '10k more "
            "a month' -> amount=10, magnitude=thousand, period=per_month."
        ),
    )

    @field_validator("direction", mode="before")
    @classmethod
    def _scrub(cls, v: Any) -> Any:
        return _scrub_tag_noise(v)


class _GoalSlots(BaseModel):
    goal_name: Optional[str] = Field(
        default=None,
        description="What they are buying or saving for, in their words, at most 6 words.",
    )
    goal_type: Optional[_GoalTypeLiteral] = Field(
        default=None, description="Best-fit category."
    )
    years: Optional[float] = Field(
        default=None,
        description=(
            "Years from NOW, but ONLY when they said it that way: 'in 5 years' "
            "-> 5, 'next year' -> 1. Leave null for a calendar year or an age — "
            "those have their own fields and the caller converts them."
        ),
    )
    target_year: Optional[int] = Field(
        default=None, description="A calendar year they named. 'by 2032' -> 2032."
    )
    target_age: Optional[int] = Field(
        default=None,
        description="THEIR age when they want it: 'marriage at 30' -> 30, 'retire by 55' -> 55.",
    )
    current_age: Optional[int] = Field(
        default=None,
        description="How old they say they are RIGHT NOW, only if stated in this message.",
    )
    cost: Optional[_Money] = Field(
        default=None,
        description="A price the CUSTOMER stated. Null if they did not state one.",
    )
    cost_estimate: Optional[_Money] = Field(
        default=None,
        description=(
            "ONLY when they named something specific enough to price but gave no "
            "number: YOUR estimate of what it costs in India today, new. Leave "
            "null if they gave a price, or if the thing is too vague to price."
        ),
    )
    inflation_pct: Optional[float] = Field(
        default=None, description="An inflation rate they specified. Usually null."
    )
    financed: Optional[bool] = Field(
        default=None,
        description="True if they will take a loan, false if paying outright, null if not discussed.",
    )
    down_payment: Optional[_Money] = Field(
        default=None, description="Down payment as an absolute amount, if given."
    )
    down_payment_pct: Optional[float] = Field(
        default=None,
        description="Down payment as a percentage of the price, if given that way.",
    )
    interest_pct: Optional[float] = Field(
        default=None, description="Loan interest rate, percent per year."
    )
    tenure_years: Optional[float] = Field(
        default=None, description="Loan tenure in years."
    )
    sip_change: Optional[bool] = Field(
        default=None,
        description=(
            "After a goal is added the advisor asks whether their SIP or "
            "income/expenses changed. True = something changed, False = nothing "
            "changed. Null if that is not the question being answered."
        ),
    )

    @field_validator("goal_type", mode="before")
    @classmethod
    def _scrub(cls, v: Any) -> Any:
        return _scrub_tag_noise(v)


class _Operation(BaseModel):
    target: _TargetLiteral = Field(
        description=(
            "'profile' — one of their stored figures/dates/preferences. "
            "'goal' — something they want to buy or save for. "
            "'plan' — the projection over everything, with nothing to change."
        )
    )
    verb: _VerbLiteral = Field(
        description=(
            "PROFILE: 'set' they stated a figure outright; 'adjust' they stated a "
            "change against what we hold ('up 20%'); 'clear' they want it removed; "
            "'read' they are asking what we have on file. "
            "GOAL: 'create' a goal not in their plan yet; 'update' a change to one "
            "that is; 'delete' remove one; 'read' list what they have. "
            "PLAN: 'project' run the projection."
        )
    )
    field_key: Optional[str] = Field(
        default=None,
        description="For target=profile: the key, copied verbatim from CAPTURABLE FIELDS.",
    )
    goal_ref: Optional[str] = Field(
        default=None,
        description=(
            "For target=goal with verb update/delete/read: which goal they mean, "
            "in their words, matched against GOALS ON FILE where possible."
        ),
    )
    value: Optional[_Money] = Field(
        default=None,
        description=(
            "For a NUMERIC profile field being set. Null for enum/date/text and "
            "for relative changes."
        ),
    )
    text_value: Optional[str] = Field(
        default=None,
        description=(
            "For an ENUM profile field: one of the listed options copied verbatim. "
            "For a DATE field: YYYY-MM-DD. For a TEXT field: their answer."
        ),
    )
    change: Optional[_Change] = Field(
        default=None,
        description="For verb=adjust ONLY: the relative change. Never resolve it yourself.",
    )
    goal: Optional[_GoalSlots] = Field(
        default=None,
        description="For target=goal: whatever this message said about it.",
    )
    confidence: float = Field(
        description=(
            "0.0 to 1.0. Be strict: 0.9+ only when they stated this plainly. "
            "Below 0.8 for anything inferred, rounded, or read from context "
            "rather than from their words."
        )
    )
    verbatim: Optional[str] = Field(
        default=None,
        description="Their own words this came from, at most 15 words.",
    )

    @field_validator("target", "verb", "field_key", mode="before")
    @classmethod
    def _scrub(cls, v: Any) -> Any:
        return _scrub_tag_noise(v)


class _LLMOutput(BaseModel):
    """Structured output schema returned by the LLM."""

    kind: _MessageKindLiteral = Field(
        description=(
            "'state' — they stated something, answered, or asked for a change. "
            "'correction' — changing something stated earlier. "
            "'confirm' — agreeing to what we read back. "
            "'reject' — what we read back is wrong. "
            "'refusal' — they declined or pushed back. "
            "'defer' — later. "
            "'cancel' — drop the goal being built entirely. "
            "'question' — asked about their plan without changing anything. "
            "'unrelated' — they changed the subject."
        )
    )
    operations: list[_Operation] = Field(
        default_factory=list,
        description=(
            "Every operation the message contains — including fields they "
            "volunteered without being asked. Empty for refusal, defer, confirm, "
            "reject and unrelated."
        ),
    )
    unchanged_fields: list[str] = Field(
        default_factory=list,
        description=(
            "Keys they explicitly said have NOT changed — 'expenses are still "
            "the same', 'everything else is unchanged'. A real answer, not a "
            "non-answer: it means do not ask about that field."
        ),
    )
    clarification: Optional[str] = Field(
        default=None,
        description=(
            "Set ONLY when a figure has NO period or scale at all and the field "
            "needs one ('I make 2.4', 'about 50'). One short question, at most 15 "
            "words. When set, leave that operation out."
        ),
    )

    @field_validator("kind", mode="before")
    @classmethod
    def _scrub(cls, v: Any) -> Any:
        return _scrub_tag_noise(v)


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------


class ResponseExtractor:
    """Reads one customer message into typed plan operations.

    Stateless and safe to build per call, like ``IntentClassifier``. The caller
    supplies the field catalogue, so the agent has no registry, no database and
    no idea where any value is stored.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        resolved_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise RuntimeError(
                "response_extractor needs an Anthropic API key "
                "(FINANCIAL_PLANNING_API_KEY or ANTHROPIC_API_KEY)."
            )
        self._llm = ChatAnthropic(
            model=model,
            api_key=resolved_key,
            max_tokens=max_tokens,
            temperature=0,  # an extractor must not sample; unset means the API default of 1.0
        ).with_structured_output(_LLMOutput)

    async def aextract(self, payload: ExtractionInput) -> ExtractionResult:
        """Read the message. Async-native, so a caller timeout really cancels it."""
        messages = [
            SystemMessage(
                content=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        # The system block is identical every turn, so it is
                        # cached; only the user block below is paid for per call.
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
            ),
            HumanMessage(content=build_user_block(payload)),
        ]
        raw: _LLMOutput = await self._llm.ainvoke(messages)
        # Re-validated through the public models so callers depend on the enums
        # in ``models.py``, never on the private tool schema above.
        return ExtractionResult.model_validate(raw.model_dump())


__all__ = ["DEFAULT_MAX_TOKENS", "DEFAULT_MODEL", "ResponseExtractor"]
