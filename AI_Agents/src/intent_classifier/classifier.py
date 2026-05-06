import os
from typing import Literal, Optional

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from .models import (
    ClassificationInput,
    ClassificationResult,
    ConversationMessage,
    Intent,
    OutOfScopeSubreason,
)
from .prompts import GOAL_PLANNING_MESSAGE, OUT_OF_SCOPE_MESSAGE, STOCK_ADVICE_MESSAGE, SYSTEM_PROMPT

load_dotenv()

_MAX_HISTORY_MESSAGES = 12


# NOTE: keep ``_IntentLiteral`` in sync with the ``Intent`` enum in
# ``models.py``. It is hard-coded here (rather than derived) because Python 3.9
# does not support ``Literal[*tuple(...)]`` unpacking. A drift test in
# ``app/services/ai_bridge/tests/test_intent_classifier_schema.py`` will fail
# loudly if the two get out of sync.
_IntentLiteral = Literal[
    "asset_allocation",
    "goal_planning",
    "stock_advice",
    "portfolio_query",
    "general_market_query",
    "rebalancing",
    "out_of_scope",
]

_OutOfScopeSubreasonLiteral = Literal[
    "gibberish",
    "identity_or_meta",
    "security_or_credentials",
    "chat_summary",
    "off_topic",
    "other",
]


class _LLMOutput(BaseModel):
    """Structured output schema returned by the LLM.

    Constraining ``intent`` to a literal causes the Anthropic tool schema to
    enforce the enum at the API level — the LLM physically cannot emit an
    unknown intent string, which avoids silently falling back to OpenAI on
    typos / hallucinated categories.
    """
    intent: _IntentLiteral = Field(description="The classified intent category.")
    confidence: float = Field(description="Confidence score between 0.0 and 1.0.")
    is_follow_up: bool = Field(
        default=False,
        description="True if the message continues the previous conversation topic; false if it starts a new topic.",
    )
    reasoning: str = Field(description="One or two sentences explaining why this intent was chosen.")
    out_of_scope_subreason: Optional[_OutOfScopeSubreasonLiteral] = Field(
        default=None,
        description=(
            "Required when intent='out_of_scope': one of gibberish, identity_or_meta, "
            "security_or_credentials, chat_summary, off_topic, other. Null otherwise."
        ),
    )


def _format_history(history: list[ConversationMessage]) -> str:
    """Convert the last N conversation messages into a compact context string."""
    if not history:
        return ""
    recent = history[-_MAX_HISTORY_MESSAGES:]
    lines = ["--- Recent Conversation History ---"]
    for msg in recent:
        label = "Customer" if msg.role == "user" else "Prozpr"
        lines.append(f"{label}: {msg.content}")
    lines.append("---")
    return "\n".join(lines)


def _build_user_turn(input: ClassificationInput) -> str:
    """Build the user turn content sent to Claude."""
    parts: list[str] = []
    history_block = _format_history(input.conversation_history)
    if history_block:
        parts.append(history_block)
    if input.active_intent:
        parts.append(f"Currently active intent: {input.active_intent.value}")
    parts.append(f"Customer's current question: {input.customer_question}")
    parts.append("\nClassify the intent using the classify_intent tool.")
    return "\n\n".join(parts)


class IntentClassifier:
    """
    Classifies a customer's financial question into one of six intents:
      - asset_allocation
      - goal_planning  (coming soon — returns a holding message)
      - stock_advice   (redirects to mutual funds)
      - portfolio_query
      - general_market_query
      - out_of_scope

    goal_planning, stock_advice, and out_of_scope each populate
    out_of_scope_message with a customer-facing canned response.

    Uses LangChain + Claude Haiku with structured output and Anthropic prompt caching.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-haiku-4-5-20251001",
    ) -> None:
        resolved_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        llm = ChatAnthropic(
            model=model,
            api_key=resolved_key,
            max_tokens=512,
        )
        self.chain = llm.with_structured_output(_LLMOutput)

    def classify(self, input: ClassificationInput) -> ClassificationResult:
        """
        Classify the customer's intent.

        Args:
            input: ClassificationInput with the customer question and optional conversation history.

        Returns:
            ClassificationResult with intent, confidence, reasoning, and (if out_of_scope)
            a customer-facing message.

        Raises:
            anthropic.APIError: On API-level failures.
        """
        messages = [
            # cache_control marks the static system prompt for Anthropic's server-side
            # prompt caching — after the first call, this block costs ~10% of normal rate.
            SystemMessage(content=[
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
            ]),
            HumanMessage(content=_build_user_turn(input)),
        ]

        raw: _LLMOutput = self.chain.invoke(messages)
        intent = Intent(raw.intent)

        _canned_responses = {
            Intent.OUT_OF_SCOPE:  OUT_OF_SCOPE_MESSAGE,
            Intent.GOAL_PLANNING: GOAL_PLANNING_MESSAGE,
            Intent.STOCK_ADVICE:  STOCK_ADVICE_MESSAGE,
        }

        subreason: Optional[OutOfScopeSubreason] = None
        if intent == Intent.OUT_OF_SCOPE:
            subreason = (
                OutOfScopeSubreason(raw.out_of_scope_subreason)
                if raw.out_of_scope_subreason
                else OutOfScopeSubreason.OTHER
            )

        return ClassificationResult(
            intent=intent,
            confidence=raw.confidence,
            is_follow_up=raw.is_follow_up,
            reasoning=raw.reasoning,
            out_of_scope_message=_canned_responses.get(intent),
            out_of_scope_subreason=subreason,
        )
