"""AI modules HTTP router — `intent_classifier.py`.

Exposes ``/api/v1/ai-modules/...`` style endpoints for debugging or direct module invocation. Not always on the live chat path; chat uses ``routers/chat`` + ``ChatBrain`` instead.
"""


from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies import CurrentUser, get_effective_user
from app.schemas.ai_modules import IntentClassifyRequest, IntentClassifyResponse
from app.services.ai_bridge.intent_classifier_service import classify_user_message

router = APIRouter(prefix="/intent-classifier", tags=["AI — Intent classifier"])


@router.post("/classify", response_model=IntentClassifyResponse)
async def classify_intent(
    payload: IntentClassifyRequest,
    _current_user: CurrentUser = Depends(get_effective_user),
):
    history = [m.model_dump() for m in payload.conversation_history]
    result = await classify_user_message(
        customer_question=payload.message,
        conversation_history=history,
    )
    return IntentClassifyResponse(
        intent=result.intent.value,
        confidence=result.confidence,
        reasoning=result.reasoning,
        out_of_scope_message=result.out_of_scope_message,
    )
