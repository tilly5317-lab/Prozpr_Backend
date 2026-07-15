"""Pydantic I/O models for the mutual_fund_query engine.

These are the shared contract between the app-layer builder (which fills a
``MutualFundQueryFacts`` from the DB + ranking CSV) and the engine orchestrator (which
narrates it). The engine itself is DB-agnostic — it only ever sees these DTOs.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ConversationTurn(BaseModel):
    """One prior chat turn, for history context."""

    role: Literal["user", "assistant"]
    content: str


class ExtractResult(BaseModel):
    """What the extract pass pulls from the customer's question + history."""

    fund_names: list[str] = Field(default_factory=list)
    asked_for: Literal["reasoning", "returns", "comparison"]


class FundReturns(BaseModel):
    """Trailing annualized (CAGR) returns; a horizon is None when unavailable."""

    return_1y_cagr_pct: Optional[float] = None
    return_3y_cagr_pct: Optional[float] = None
    return_5y_cagr_pct: Optional[float] = None


class FundFacts(BaseModel):
    """Grounded facts for one fund. Numbers here are the ONLY numbers the
    narration may state."""

    fund_name: str
    sub_category: Optional[str] = None
    returns: Optional[FundReturns] = None
    house_reason: Optional[str] = None       # our selection_reason, if in the shortlist
    shortlist_rank: Optional[int] = None     # our house rank — NOT a category percentile
    has_house_view: bool = False


class PeerFund(BaseModel):
    """A lightweight like-for-like peer row (auto-derived category peers)."""

    fund_name: str
    return_3y_cagr_pct: Optional[float] = None
    shortlist_rank: Optional[int] = None


class MutualFundQueryFacts(BaseModel):
    """The facts container the narration renders.

    ``funds`` holds the explicitly-asked funds (one, or several for a head-to-head).
    ``peers`` holds auto-derived category peers — populated only for a single-fund
    comparison; empty when 2+ funds were named (the named funds are the set).
    """

    funds: list[FundFacts] = Field(default_factory=list)
    peers: list[PeerFund] = Field(default_factory=list)


class MutualFundQueryResponse(BaseModel):
    """The engine's output — a narrated answer, or a clarifying question."""

    answer: str
    clarifying_question: Optional[str] = None
