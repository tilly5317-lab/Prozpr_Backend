"""ChatSendMessageResponse exposes additional_investment_run_id for the Save pill.

AINV twin of app/domains/chat/tests/test_send_message_rebalancing_id.py — mirrors
ideal_allocation_rebalancing_id's wiring: ChatHandlerResult -> ModuleOutput ->
ChatBrainResult -> this schema.
"""

from __future__ import annotations

import uuid

from app.domains.chat.schemas.chat import ChatSendMessageResponse


def test_send_response_exposes_additional_investment_run_id():
    assert "additional_investment_run_id" in ChatSendMessageResponse.model_fields
    rid = uuid.uuid4()
    resp = ChatSendMessageResponse.model_construct(additional_investment_run_id=rid)
    assert resp.additional_investment_run_id == rid
