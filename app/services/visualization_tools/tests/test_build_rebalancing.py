"""Smoke tests for build_rebalancing.build_charts_for_rebalancing."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.visualization_tools.build_rebalancing import (
    build_charts_for_rebalancing,
)


@pytest.mark.asyncio
async def test_empty_names_returns_empty():
    fake_response = MagicMock()
    out = await build_charts_for_rebalancing(fake_response, [])
    assert out == []


@pytest.mark.asyncio
async def test_unknown_name_skipped():
    fake_response = MagicMock()
    out = await build_charts_for_rebalancing(fake_response, ["does_not_exist"])
    assert out == []
