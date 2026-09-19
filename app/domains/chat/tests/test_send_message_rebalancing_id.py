"""ChatSendMessageResponse exposes ideal_allocation_rebalancing_id for the Save pill."""

from __future__ import annotations

import uuid

from app.domains.chat.schemas.chat import ChatSendMessageResponse


def test_send_response_exposes_ideal_allocation_rebalancing_id():
    assert "ideal_allocation_rebalancing_id" in ChatSendMessageResponse.model_fields
    rid = uuid.uuid4()
    resp = ChatSendMessageResponse.model_construct(ideal_allocation_rebalancing_id=rid)
    assert resp.ideal_allocation_rebalancing_id == rid
