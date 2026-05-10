"""Pydantic types — chart_selector agent inputs and outputs."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ChartCatalogueEntry(BaseModel):
    """One chart that can be picked. The bridge builds these from the live registry."""

    name: str
    description: str


class ChartSelectionInput(BaseModel):
    """Everything the selector needs to pick relevant charts for one turn."""

    question: str
    intent: Optional[str] = None
    catalogue: list[ChartCatalogueEntry]


class ChartSelectionResult(BaseModel):
    """LLM tool-call output. Names may be invalid; the bridge validates against the registry."""

    chart_names: list[str] = Field(default_factory=list)
