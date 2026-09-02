"""Client contract handling and row coercion, without touching the network.

The 403 tests matter most: Cloudflare and VR both answer 403, they mean
completely different things, and telling them apart by hand at 03:00 is how an
outage gets misdiagnosed as a contract problem.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.domains.vr_data.client import (
    MAX_INCREMENTAL_WINDOW_DAYS,
    VrAccessError,
    VrClient,
    VrError,
    VrNotConfigured,
    format_window,
)
from app.domains.vr_data.services.sync_service import _chunk_size, _coerce_row


class _StubResponse:
    def __init__(self, status_code, *, text="", headers=None, payload=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _StubHttp:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def get(self, url, params=None):
        self.calls.append((url, params))
        return self._response


def _client(response) -> VrClient:
    client = VrClient(api_key="test-key")
    client._client = _StubHttp(response)  # noqa: SLF001 - test seam
    return client


# ---------------------------------------------------------------------------
# 403 discrimination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cloudflare_403_is_reported_as_never_reaching_vr():
    client = _client(
        _StubResponse(
            403,
            text="<!DOCTYPE html><html>Attention Required! | Cloudflare</html>",
            headers={"content-type": "text/html; charset=UTF-8"},
        )
    )
    with pytest.raises(VrAccessError) as exc:
        await client.count("nav")
    assert exc.value.reached_vr is False
    assert "whitelisted" in str(exc.value)


@pytest.mark.asyncio
async def test_vr_json_403_is_reported_as_a_contract_refusal():
    client = _client(
        _StubResponse(
            403,
            text='{"error":"table not permitted for this key"}',
            headers={"content-type": "application/json"},
            payload={"error": "table not permitted for this key"},
        )
    )
    with pytest.raises(VrAccessError) as exc:
        await client.count("securities")
    assert exc.value.reached_vr is True
    assert exc.value.table == "securities"


# ---------------------------------------------------------------------------
# contract quirks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_page_always_sends_output_data():
    """VR defaults ``output`` to ``count``; a page that forgets it returns none."""
    client = _client(_StubResponse(200, payload={"data": [{"plan_id": "1"}]}))
    await client.fetch_page("nav")
    _, params = client._client.calls[0]  # noqa: SLF001
    assert params["output"] == "data"


@pytest.mark.asyncio
async def test_changed_after_older_than_ninety_days_is_refused_locally():
    """Refused before spending a request — VR would reject it anyway, and the
    real answer is the bulk route."""
    client = _client(_StubResponse(200, payload={"data": []}))
    stale = date.today() - timedelta(days=MAX_INCREMENTAL_WINDOW_DAYS + 5)
    with pytest.raises(VrError, match="bulk"):
        await client.fetch_page("nav", changed_after=stale)
    assert client._client.calls == []  # noqa: SLF001


@pytest.mark.asyncio
async def test_changed_before_requires_changed_after():
    client = _client(_StubResponse(200, payload={"data": []}))
    with pytest.raises(VrError, match="only valid with"):
        await client.fetch_page("nav", changed_before=date.today())


@pytest.mark.asyncio
async def test_self_referential_next_link_terminates_the_walk():
    """Some VR responses echo the current page as ``next``; following it loops."""
    url = "https://valueresearchapi.in/v1/nav?page=9"
    client = _client(
        _StubResponse(200, payload={"data": [{"plan_id": "1"}], "links": {"next": url}})
    )
    page = await client.fetch_page("nav", url=url)
    assert page.next_url is None


@pytest.mark.asyncio
async def test_missing_key_is_a_clean_configuration_error():
    client = VrClient(api_key=None)
    assert client.configured is False
    with pytest.raises(VrNotConfigured):
        await client.fetch_page("nav")


def test_window_formats():
    assert format_window(date(2026, 9, 2)) == "2026-09-02"
    assert format_window(datetime(2026, 9, 2, 14, 30)) == "2026-09-02-14-30"


# ---------------------------------------------------------------------------
# coercion
# ---------------------------------------------------------------------------


def test_blank_and_placeholder_strings_become_null_not_zero():
    """VR sends ``""`` and ``-`` for absent values; storing 0 would be a lie
    that propagates into returns and allocations."""
    types = {"nav": "numeric", "nav_date": "date", "plan_id": "text"}
    row, failures = _coerce_row(
        {"nav": "", "nav_date": "-", "plan_id": "123"}, types
    )
    assert row == {"nav": None, "nav_date": None, "plan_id": "123"}
    assert failures == 0


def test_numeric_strings_with_commas_parse():
    types = {"latest_aum": "numeric"}
    row, failures = _coerce_row({"latest_aum": "12,345.67"}, types)
    assert row["latest_aum"] == Decimal("12345.67")
    assert failures == 0


def test_unparseable_value_is_nulled_and_counted_not_raised():
    """One bad field must not abort a 5000-row page."""
    types = {"nav": "numeric"}
    row, failures = _coerce_row({"nav": "N.A."}, types)
    assert row["nav"] is None
    assert failures == 1


def test_multiple_date_formats_parse():
    types = {"nav_date": "date"}
    for raw in ("2026-09-02", "02-09-2026", "02/09/2026", "02-Sep-2026"):
        row, failures = _coerce_row({"nav_date": raw}, types)
        assert row["nav_date"] == date(2026, 9, 2), raw
        assert failures == 0


def test_nested_json_survives_as_a_string_rather_than_being_lost():
    types = {"sector_details": "jsonb"}
    row, _ = _coerce_row({"sector_details": '{"Financials": 22.4}'}, types)
    assert row["sector_details"] == {"Financials": 22.4}
    row, _ = _coerce_row({"sector_details": "not json at all"}, types)
    assert row["sector_details"] == "not json at all"


def test_unknown_vr_fields_are_dropped_rather_than_crashing_the_insert():
    types = {"plan_id": "text"}
    row, _ = _coerce_row({"plan_id": "1", "brand_new_vr_field": "x"}, types)
    assert row == {"plan_id": "1"}


def test_chunk_size_stays_under_the_asyncpg_parameter_ceiling():
    """asyncpg refuses >32767 bound parameters; fund_basic_details is 84 wide."""
    for width in (4, 26, 84, 200):
        assert _chunk_size(width) * width <= 32_000
        assert _chunk_size(width) >= 1


# ---------------------------------------------------------------------------
# passthrough guardrails
# ---------------------------------------------------------------------------


def test_window_parser_accepts_vrs_two_documented_formats():
    from datetime import date as _date

    from app.domains.vr_data.routers.vr_admin_router import _parse_window

    assert _parse_window("2026-09-02", "changed_after") == _date(2026, 9, 2)
    assert _parse_window("2026-09-02-14-30", "changed_after") == datetime(
        2026, 9, 2, 14, 30
    )
    assert _parse_window(None, "changed_after") is None
    assert _parse_window("   ", "changed_after") is None


def test_window_parser_rejects_a_typo_locally():
    """A bad window must 400, not spend one of the 500/hour requests."""
    from fastapi import HTTPException

    from app.domains.vr_data.routers.vr_admin_router import _parse_window

    with pytest.raises(HTTPException) as exc:
        _parse_window("02/09/2026", "changed_after")
    assert exc.value.status_code == 400


@pytest.mark.parametrize(
    "name,allowed",
    [
        ("plan_id", True),
        ("as_on_date-GREATER-THAN", True),
        ("nav-LESS-THAN", True),
        ("", False),
        ("1bad", False),
        ("drop table", False),
        ("../../etc/passwd", False),
        ("a" * 80, False),
    ],
)
def test_passthrough_only_forwards_filter_shaped_params(name, allowed):
    """Anything not shaped like a VR field filter is rejected, not proxied."""
    from app.domains.vr_data.routers.vr_admin_router import _SAFE_PARAM

    assert bool(_SAFE_PARAM.match(name)) is allowed
