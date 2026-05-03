"""Shared base schema for chart payloads.

Every chart's Pydantic payload subclasses ``ChartBase`` so the discriminated
union in ``registry.py`` can rely on a stable shape (``schema_version``,
``title``, ``subtitle``) regardless of which chart it is.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

SCHEMA_VERSION = "v1"


class ChartBase(BaseModel):
    schema_version: Literal["v1"] = "v1"
    title: str
    subtitle: str | None = None
