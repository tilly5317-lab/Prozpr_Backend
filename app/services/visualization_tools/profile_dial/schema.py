"""Pydantic payload — profile_dial chart."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.services.visualization_tools._base import ChartBase


class ProfileDial(ChartBase):
    type: Literal["profile_dial"] = "profile_dial"
    score: float = Field(..., ge=0, le=100)
    band: Literal[
        "Conservative",
        "Moderate-Conservative",
        "Balanced",
        "Moderate-Aggressive",
        "Aggressive",
    ]
    headline: str
