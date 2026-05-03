"""Smoke tests for build_aa.build_charts_for_aa."""
from __future__ import annotations

import uuid

import pytest

from app.services.visualization_tools.build_aa import build_charts_for_aa


@pytest.mark.asyncio
async def test_empty_names_returns_empty(db_session):
    user_id = uuid.uuid4()
    out = await build_charts_for_aa(db_session, user_id, [])
    assert out == []


@pytest.mark.asyncio
async def test_unknown_name_skipped(db_session):
    user_id = uuid.uuid4()
    out = await build_charts_for_aa(db_session, user_id, ["does_not_exist"])
    assert out == []
