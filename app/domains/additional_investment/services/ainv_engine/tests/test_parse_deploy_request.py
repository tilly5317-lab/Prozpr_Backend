"""Input→expected table for parse_deploy_request — deterministic deploy amount +
cadence parsing (no LLM). Indian money expressions map to a float amount; SIP /
monthly phrasing selects Cadence.SIP_MONTHLY, else Cadence.LUMPSUM."""

from __future__ import annotations

import pytest

from app.domains.ai_engine.common import ensure_ai_agents_path

ensure_ai_agents_path()

from additional_investment.models import Cadence  # type: ignore[import-not-found]  # noqa: E402

from app.domains.additional_investment.services.ainv_engine.chat import (  # noqa: E402
    parse_deploy_request,
)


@pytest.mark.parametrize(
    "question, expected_amount",
    [
        ("₹5L", 500000.0),
        ("2 lakh", 200000.0),
        ("50k", 50000.0),
        ("1 crore", 10000000.0),
        ("Rs 2,00,000", 200000.0),
        ("invest 75000", 75000.0),
        ("invest 50000 for the long term", 50000.0),  # 'l' in 'long' is NOT lakh (Finding 1)
        ("where should I put my money?", None),
    ],
)
def test_parse_amount(question, expected_amount):
    amount, _cadence = parse_deploy_request(question)
    assert amount == expected_amount


@pytest.mark.parametrize(
    "question, expected_cadence",
    [
        ("invest 50k monthly", Cadence.SIP_MONTHLY),
        ("start a SIP of 10000", Cadence.SIP_MONTHLY),
        ("put 5000 per month", Cadence.SIP_MONTHLY),
        ("deploy 5000/month", Cadence.SIP_MONTHLY),
        ("invest 5L as lumpsum", Cadence.LUMPSUM),
        ("invest 5L", Cadence.LUMPSUM),
    ],
)
def test_parse_cadence(question, expected_cadence):
    _amount, cadence = parse_deploy_request(question)
    assert cadence == expected_cadence
