"""Agent working memory — persists across turns via LangGraph checkpointer."""
from __future__ import annotations
from datetime import date
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel

from goal_planning.models import (
    GoalPlanningInput, GoalPlanningOutput, OneOffEvent,
    OverrideSpec, GoalMutation, CustomGoal, GoalProperty, Lever,
)


class CapturedCashflow(BaseModel):
    event: OneOffEvent
    direction: Literal["in", "out"]


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]

    # Refreshed each turn
    baseline_input: GoalPlanningInput
    anchor_date: date

    # Persisted across turns
    accumulated_overrides: list[OverrideSpec]
    captured_goals: list[CustomGoal]
    captured_properties: list[GoalProperty]
    captured_cashflows: list[CapturedCashflow]
    captured_mutations: list[GoalMutation]

    # Computed within turn
    last_output: GoalPlanningOutput | None
    last_levers: list[Lever]

    # Control
    dirty: bool
    error_log: list[str]
