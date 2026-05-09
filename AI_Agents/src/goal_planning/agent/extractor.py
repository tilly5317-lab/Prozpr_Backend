"""NL → ExtractedFinancialEvent | ExtractionError."""
from __future__ import annotations
import asyncio
from datetime import date
from typing import Any

import anthropic
from langchain_anthropic import ChatAnthropic
from langchain_core.exceptions import OutputParserException
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel, Field, ValidationError
from rapidfuzz import fuzz

from goal_planning.models import (
    ExtractedFinancialEvent, ExtractionError,
    ExtractedGoal, ExtractedProperty, ExtractedCashflow, ExtractedMutation,
)
from goal_planning.agent.prompts import EXTRACTOR_SYSTEM_PROMPT


# Defaults & constants (will be sourced from config.py in Phase 4)
EXTRACTOR_MODEL = "claude-haiku-4-5-20251001"
FUZZY_MATCH_THRESHOLD = 85
DEFAULT_PROPERTY_DOWNPAYMENT_PCT = 20.0
DEFAULT_MORTGAGE_TENURE_YEARS = 20
DEFAULT_MORTGAGE_INTEREST_ANNUAL = 0.085


class _ExtractionEnvelope(BaseModel):
    """Wrapper class around the discriminated union so LangChain's
    structured-output tooling (which expects a single Pydantic class) can
    accept a class object instead of an Annotated[Union, ...] type alias."""

    event: ExtractedFinancialEvent = Field(
        description=(
            "The extracted financial event. Choose the matching kind: "
            "custom_goal, property_goal, cashflow_event, or goal_mutation."
        ),
    )


def _normalize(name: str) -> str:
    """Lowercase + strip common stop words for fuzzy matching."""
    stops = {"the", "my", "a", "an", "fund", "goal", "for"}
    return " ".join(w for w in name.casefold().split() if w not in stops)


class FinancialEventExtractor:
    def __init__(self, model: str = EXTRACTOR_MODEL):
        self._llm = ChatAnthropic(model=model, temperature=0)
        self._chain = self._build_chain()

    def _build_chain(self):
        prompt = ChatPromptTemplate.from_messages([
            ("system", EXTRACTOR_SYSTEM_PROMPT),
            ("human", "{description}"),
        ])
        structured = self._llm.with_structured_output(_ExtractionEnvelope)
        unwrap = RunnableLambda(lambda env: env.event)
        return prompt | structured | unwrap

    async def extract(
        self,
        description: str,
        anchor_date: date,
        existing_goal_names: list[str],
    ) -> ExtractedFinancialEvent | ExtractionError:
        try:
            result = await asyncio.to_thread(self._chain.invoke, {
                "description": description,
                "anchor_date": anchor_date.isoformat(),
                "existing_goal_names": ", ".join(existing_goal_names) or "(none)",
                "default_property_downpayment_pct": DEFAULT_PROPERTY_DOWNPAYMENT_PCT,
                "default_mortgage_tenure_years": DEFAULT_MORTGAGE_TENURE_YEARS,
                "default_mortgage_interest": DEFAULT_MORTGAGE_INTEREST_ANNUAL,
            })
        except (OutputParserException, ValidationError, anthropic.APIError) as e:
            return ExtractionError(kind="error", reason=f"Could not parse: {e}")
        except Exception as e:
            return ExtractionError(kind="error", reason=f"Unexpected error: {e}")

        # Fuzzy collision check → promote to mutation
        if isinstance(result, (ExtractedGoal, ExtractedProperty)):
            new_name = result.goal.name if isinstance(result, ExtractedGoal) else result.property.name
            best_match = self._best_fuzzy_match(new_name, existing_goal_names)
            if best_match:
                return ExtractedMutation(
                    kind="goal_mutation", op="update",
                    goal_name=best_match,
                    fields=self._diff_against_existing(result),
                )

        # Past-date guard via dated_field accessor
        d = result.dated_field()
        if d is not None and d < anchor_date:
            return ExtractionError(kind="error", reason=f"Date {d.isoformat()} is in the past")

        return result

    def _best_fuzzy_match(self, new_name: str, existing: list[str]) -> str | None:
        best_score = 0
        best_match = None
        normalized_new = _normalize(new_name)
        for name in existing:
            score = fuzz.token_set_ratio(normalized_new, _normalize(name))
            if score > best_score:
                best_score = score
                best_match = name
        return best_match if best_score >= FUZZY_MATCH_THRESHOLD else None

    def _diff_against_existing(self, result: ExtractedFinancialEvent) -> dict[str, Any]:
        """Extract user-provided fields for the mutation."""
        if isinstance(result, ExtractedGoal):
            return result.goal.model_dump(exclude_unset=True, exclude={"name"})
        if isinstance(result, ExtractedProperty):
            return result.property.model_dump(exclude_unset=True, exclude={"name"})
        return {}
