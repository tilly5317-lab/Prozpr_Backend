import os
from typing import Optional

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from .models import ClassificationInput, ClassificationResult, ConversationMessage, Intent
from .prompts import GOAL_PLANNING_MESSAGE, OUT_OF_SCOPE_MESSAGE, STOCK_ADVICE_MESSAGE, SYSTEM_PROMPT

load_dotenv()

_MAX_HISTORY_MESSAGES = 6


class _LLMOutput(BaseModel):
    """Structured output schema returned by the LLM."""
    intent: str = Field(description="The classified intent category.")
    confidence: float = Field(description="Confidence score between 0.0 and 1.0.")
    is_follow_up: bool = Field(
        default=False,
        description="True if the message continues the previous conversation topic; false if it starts a new topic.",
    )
    reasoning: str = Field(description="One or two sentences explaining why this intent was chosen.")


def _format_history(history: list[ConversationMessage]) -> str:
    """Convert the last N conversation messages into a compact context string."""
    if not history:
        return ""
    recent = history[-_MAX_HISTORY_MESSAGES:]
    lines = ["--- Recent Conversation History ---"]
    for msg in recent:
        label = "Customer" if msg.role == "user" else "Prozper"
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
      - portfolio_optimisation
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
            max_tokens=150,
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

        return ClassificationResult(
            intent=intent,
            confidence=raw.confidence,
            is_follow_up=raw.is_follow_up,
            reasoning=raw.reasoning,
            out_of_scope_message=_canned_responses.get(intent),
        )
