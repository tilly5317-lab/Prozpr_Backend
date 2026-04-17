from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.profile import InvestmentProfile, PersonalFinanceProfile, RiskProfile
from app.models.user import User
from app.services.ai_module_telemetry import record_ai_module_run
from app.services.effective_risk_profile import maybe_recalculate_effective_risk

logger = logging.getLogger(__name__)


ONBOARDING_QUESTION_CATALOG: list[dict[str, str]] = [
    {
        "field": "annual_income",
        "label": "Annual income",
        "question": "What is your current annual income, approximately?",
        "section": "financial_picture",
    },
    {
        "field": "annual_expenses",
        "label": "Annual expenses",
        "question": "What are your annual expenses currently?",
        "section": "financial_picture",
    },
    {
        "field": "investable_assets",
        "label": "Investable assets",
        "question": "How much do you currently have in investable assets?",
        "section": "financial_picture",
    },
    {
        "field": "total_liabilities",
        "label": "Total liabilities",
        "question": "Do you have any loans or liabilities? Rough total is fine.",
        "section": "financial_picture",
    },
    {
        "field": "property_value",
        "label": "Property value",
        "question": "Do you own property? If yes, what is the approximate property value?",
        "section": "financial_picture",
    },
    {
        "field": "mortgage_amount",
        "label": "Mortgage outstanding",
        "question": "How much mortgage is outstanding on that property?",
        "section": "financial_picture",
    },
    {
        "field": "planned_major_expenses",
        "label": "Planned major expenses",
        "question": "Any major expenses planned in the next 1-3 years?",
        "section": "financial_picture",
    },
    {
        "field": "emergency_fund",
        "label": "Emergency fund amount",
        "question": "How much emergency fund do you currently keep?",
        "section": "financial_picture",
    },
    {
        "field": "emergency_fund_months",
        "label": "Emergency fund months",
        "question": "How many months of expenses does that emergency fund cover?",
        "section": "financial_picture",
    },
    {
        "field": "has_health_insurance",
        "label": "Has health insurance",
        "question": "Do you currently have health insurance coverage?",
        "section": "financial_picture",
    },
    {
        "field": "selected_goals",
        "label": "Investment goals",
        "question": "What are your key investment goals right now?",
        "section": "goals",
    },
    {
        "field": "investment_horizon",
        "label": "Investment horizon",
        "question": "What is your typical investment horizon for these goals?",
        "section": "goals",
    },
    {
        "field": "investment_experience",
        "label": "Investment experience",
        "question": "How would you describe your investment experience?",
        "section": "risk",
    },
    {
        "field": "drop_reaction",
        "label": "Reaction to 20% drop",
        "question": "If your portfolio drops by around 20%, what would you likely do?",
        "section": "risk",
    },
    {
        "field": "risk_level_label",
        "label": "Risk preference",
        "question": "Would you describe your risk preference as conservative, moderate, or aggressive?",
        "section": "risk",
    },
]

_RISK_LABEL_TO_LEVEL: dict[str, int] = {
    "conservative": 0,
    "moderately conservative": 1,
    "moderate": 2,
    "moderately aggressive": 3,
    "aggressive": 4,
}

_MAX_HISTORY_CHARS = 12_000
_ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
_DEFAULT_ONBOARDING_MODEL_CANDIDATES = (
    "claude-3-5-haiku-latest",
    "claude-3-5-haiku-20241022",
    "claude-3-5-sonnet-latest",
    "claude-3-5-sonnet-20241022",
    "claude-3-haiku-20240307",
)


@dataclass
class OnboardingTurnResult:
    assistant_message: str
    phase: str
    next_question: str | None
    extracted_values: list[dict[str, Any]]
    assumptions: list[dict[str, Any]]
    advisories: list[str]
    summary_rows: list[dict[str, Any]]
    ready_for_confirmation: bool
    committed: bool
    write_payload_preview: dict[str, Any]


def _history_to_text(history: list[dict[str, str]]) -> str:
    rendered = []
    for row in history:
        role = row.get("role", "user").upper()
        content = (row.get("content") or "").strip()
        if not content:
            continue
        rendered.append(f"{role}: {content}")
    text = "\n".join(rendered)
    if len(text) > _MAX_HISTORY_CHARS:
        return text[-_MAX_HISTORY_CHARS:]
    return text


def _extract_json_payload(raw_text: str) -> dict[str, Any]:
    """
    Parse JSON from LLM text that may include markdown fences or surrounding prose.
    """
    text = (raw_text or "").strip()
    if not text:
        raise json.JSONDecodeError("empty response", "", 0)

    # Fast path: already pure JSON.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    candidates: list[str] = []

    # Strip common fenced-block wrappers.
    if text.startswith("```"):
        fence_lines = text.splitlines()
        if fence_lines:
            # Drop opening fence.
            body_lines = fence_lines[1:]
            # Drop trailing fence if present.
            if body_lines and body_lines[-1].strip().startswith("```"):
                body_lines = body_lines[:-1]
            candidates.append("\n".join(body_lines).strip())

    # Extract first explicit fenced json block.
    fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, flags=re.IGNORECASE)
    if fence_match:
        candidates.append(fence_match.group(1).strip())

    # Extract widest object range from first '{' to last '}'.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(text[start : end + 1].strip())

    for chunk in candidates:
        if not chunk:
            continue
        try:
            return json.loads(chunk)
        except json.JSONDecodeError:
            continue

    # Raise on original text for better caller logs.
    return json.loads(text)


_FALLBACK_QUESTIONS = [
    "Got it! Could you tell me a bit about any loans or savings you have?",
    "Thanks! How about your investment goals — what are you saving towards?",
    "Awesome, and would you say you're a conservative, moderate, or aggressive investor?",
    "Great stuff! Any health insurance, and roughly how many months of emergency savings?",
    "Nearly there! What's your investment horizon — short-term, medium, or long-term?",
]
_fallback_q_index = 0


def _deterministic_fallback_extract(
    latest_user_answer: str,
    accumulated_values: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Cheap parser fallback when LLM output is malformed/unavailable."""
    global _fallback_q_index
    text = latest_user_answer.lower()
    extracted: list[dict[str, Any]] = list(accumulated_values or [])

    lpa_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:lpa|l(?:akh|ac)?\s*(?:per\s*annum|pa|p\.a\.?))",
        text,
        flags=re.IGNORECASE,
    )
    cr_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:cr(?:ore)?|Cr)",
        latest_user_answer,
        flags=re.IGNORECASE,
    )
    amount_match = re.search(
        r"(?:rs|inr|₹)?\s*([0-9][0-9,]{4,})",
        latest_user_answer,
        flags=re.IGNORECASE,
    )

    amount = None
    if lpa_match:
        amount = float(lpa_match.group(1)) * 100_000
    elif cr_match:
        amount = float(cr_match.group(1)) * 10_000_000
    elif amount_match:
        amount = _safe_number(amount_match.group(1))

    already_captured = {v.get("field") for v in extracted}

    if amount is not None:
        if "income" in text and "annual_income" not in already_captured:
            extracted.append(
                {"field": "annual_income", "label": "Annual income", "value": amount,
                 "confidence": 0.5, "status": "needs_confirmation", "section": "financial_picture"}
            )
        elif "expense" in text and "annual_expenses" not in already_captured:
            extracted.append(
                {"field": "annual_expenses", "label": "Annual expenses", "value": amount,
                 "confidence": 0.5, "status": "needs_confirmation", "section": "financial_picture"}
            )
        elif ("property" in text or "home" in text or "house" in text) and "property_value" not in already_captured:
            extracted.append(
                {"field": "property_value", "label": "Property value", "value": amount,
                 "confidence": 0.45, "status": "needs_confirmation", "section": "financial_picture"}
            )

    if any(w in text for w in ("goal", "saving for", "plan")) and "selected_goals" not in already_captured:
        extracted.append(
            {"field": "selected_goals", "label": "Investment goals",
             "value": [latest_user_answer.strip()], "confidence": 0.35,
             "status": "needs_confirmation", "section": "goals"}
        )

    question = _FALLBACK_QUESTIONS[_fallback_q_index % len(_FALLBACK_QUESTIONS)]
    _fallback_q_index += 1

    return {
        "phase": "collecting",
        "assistant_guidance": "",
        "next_question": question,
        "ready_for_confirmation": len(extracted) >= 6,
        "extracted_values": extracted,
        "assumptions": [],
        "advisories": [],
    }


def _safe_number(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        cleaned = (
            v.replace("₹", "")
            .replace("INR", "")
            .replace("inr", "")
            .replace(",", "")
            .strip()
        )
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _risk_level_from_label(raw: Any) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        n = int(raw)
        return n if 0 <= n <= 4 else None
    key = str(raw).strip().lower()
    return _RISK_LABEL_TO_LEVEL.get(key)


def _string_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        out = [str(x).strip() for x in value if str(x).strip()]
        return out or None
    if isinstance(value, str):
        parts = [p.strip() for p in value.replace(";", ",").split(",")]
        out = [p for p in parts if p]
        return out or None
    return None


def _build_write_payload(extracted_values: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    value_map: dict[str, Any] = {}
    for item in extracted_values:
        field = item.get("field")
        if isinstance(field, str):
            value_map[field] = item.get("value")

    annual_income = _safe_number(value_map.get("annual_income"))
    annual_expenses = _safe_number(value_map.get("annual_expenses"))
    selected_goals = _string_list(value_map.get("selected_goals"))
    investment_horizon = (
        str(value_map.get("investment_horizon")).strip()
        if value_map.get("investment_horizon") is not None
        else None
    )

    onboarding_profile_payload: dict[str, Any] = {}
    if annual_income is not None:
        onboarding_profile_payload["annual_income_min"] = annual_income
        onboarding_profile_payload["annual_income_max"] = annual_income
    if annual_expenses is not None:
        onboarding_profile_payload["annual_expense_min"] = annual_expenses
        onboarding_profile_payload["annual_expense_max"] = annual_expenses
    if selected_goals is not None:
        onboarding_profile_payload["selected_goals"] = selected_goals
    if investment_horizon:
        onboarding_profile_payload["investment_horizon"] = investment_horizon

    investment_payload: dict[str, Any] = {}
    for field in (
        "investable_assets",
        "total_liabilities",
        "property_value",
        "mortgage_amount",
        "planned_major_expenses",
        "emergency_fund",
    ):
        n = _safe_number(value_map.get(field))
        if n is not None:
            investment_payload[field] = n
    if value_map.get("emergency_fund_months") is not None:
        investment_payload["emergency_fund_months"] = str(value_map["emergency_fund_months"]).strip()
    if annual_income is not None:
        investment_payload["annual_income"] = annual_income

    risk_payload: dict[str, Any] = {}
    for field in ("investment_experience", "investment_horizon", "drop_reaction"):
        if value_map.get(field) is not None:
            risk_payload[field] = str(value_map[field]).strip()
    risk_level = _risk_level_from_label(value_map.get("risk_level_label"))
    if risk_level is not None:
        risk_payload["risk_level"] = risk_level

    return {
        "onboarding_profile": onboarding_profile_payload,
        "investment_profile": investment_payload,
        "risk_profile": risk_payload,
    }


def _summary_rows_from_values(extracted_values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in extracted_values:
        field = item.get("field")
        if not field:
            continue
        rows.append(
            {
                "field": field,
                "label": item.get("label") or field,
                "value": item.get("value"),
                "confidence": item.get("confidence"),
                "status": item.get("status") or "captured",
                "section": item.get("section") or "general",
            }
        )
    return rows


def _compose_assistant_message(
    phase: str,
    guidance: str,
    next_question: str | None,
    assumptions: list[dict[str, Any]],
    advisories: list[str],
) -> str:
    # UX requirement: keep assistant output minimal - only a friendly question.
    if phase == "completed":
        return "Awesome, all set. Would you like to update anything else?"
    if phase == "review":
        return "You're doing great - does this summary look correct to you?"
    if next_question and next_question.strip():
        return next_question.strip()
    fallback = guidance.strip()
    if fallback.endswith("?"):
        return fallback
    return "Thanks! Could you share a little more so I can capture this accurately?"


async def _fetch_available_anthropic_models(api_key: str) -> list[str]:
    """Best-effort model discovery to avoid repeated 404 model errors."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                _ANTHROPIC_MODELS_URL,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            )
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return []
        out: list[str] = []
        for row in rows:
            if isinstance(row, dict):
                mid = row.get("id")
                if isinstance(mid, str) and mid.strip():
                    out.append(mid.strip())
        return out
    except Exception as exc:
        logger.warning("onboarding_haiku_model_list_failed: %s", exc)
        return []


def _prioritize_available_models(available: list[str], preferred: list[str]) -> list[str]:
    if not available:
        return preferred

    # Preserve explicit preference first when available.
    chosen: list[str] = [m for m in preferred if m in available]

    # Then add best likely alternatives from account-visible catalog.
    for token in ("haiku", "sonnet", "claude"):
        for m in available:
            lm = m.lower()
            if token in lm and m not in chosen:
                chosen.append(m)
    return chosen or preferred


async def _call_haiku_extract(
    *,
    question_catalog: list[dict[str, str]],
    conversation_history: list[dict[str, str]],
    latest_user_answer: str,
    action: str,
    user_id: UUID,
    session_id: UUID,
    db: AsyncSession | None,
    accumulated_values: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    api_key = get_settings().get_anthropic_key()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required for onboarding chat extraction.")

    system_prompt = (
        "You are Tilly — a warm, friendly financial info-taker with 30+ years of experience.\n"
        "PERSONALITY: Talk like a caring friend, not a bank officer. Use casual language.\n"
        "If the user says 'no' or declines, respect it immediately — mark it 0 and move on.\n"
        "If the user is vague, make a reasonable assumption and mark confidence low.\n"
        "NEVER repeat a question the user already answered in conversation_history.\n"
        "NEVER ask more than ONE follow-up question per turn.\n"
        "CRITICAL: Group 2-3 related topics in your next_question when possible.\n"
        "Example good question: 'Nice! Do you have any loans, and roughly how much emergency savings do you keep?'\n"
        "Example bad question: 'What is your total liabilities?' (too formal, only one topic)\n"
        "When the user has answered enough fields (6+), set ready_for_confirmation=true.\n"
        "OUTPUT: Return ONLY raw JSON (no markdown fences, no ```json wrapper, no explanation text).\n"
        "The JSON must be compact — keep values short strings/numbers, not paragraphs."
    )

    catalog_compact = [
        {"f": q["field"], "l": q["label"], "s": q["section"]}
        for q in question_catalog
    ]

    acc_compact = []
    for v in (accumulated_values or []):
        acc_compact.append({"f": v.get("field"), "v": v.get("value"), "c": v.get("confidence")})

    user_prompt_text = (
        f"ACTION: {action}\n"
        f"FIELDS_CATALOG: {json.dumps(catalog_compact)}\n"
        f"ALREADY_CAPTURED: {json.dumps(acc_compact)}\n"
        f"CONVERSATION_SO_FAR:\n{_history_to_text(conversation_history)}\n"
        f"LATEST_USER_ANSWER: {latest_user_answer}\n\n"
        "Return JSON with this exact shape:\n"
        '{"phase":"collecting|review",'
        '"next_question":"one friendly casual question covering 2-3 remaining fields OR null if done",'
        '"ready_for_confirmation":true/false,'
        '"extracted_values":[{"field":"...","label":"...","value":"...","confidence":0.0-1.0,"status":"captured|assumed","section":"..."}],'
        '"assumptions":[],"advisories":[]}\n\n'
        "RULES:\n"
        "- Include ALL previously extracted values from conversation history, not just this turn\n"
        "- If user declines a field, set value to 'not disclosed', confidence 0\n"
        "- If 6+ fields captured, set ready_for_confirmation=true and next_question=null\n"
        "- Keep next_question under 30 words, warm and casual\n"
        "- Output raw JSON only, no markdown fences"
    )

    configured_model = (os.getenv("ONBOARDING_HAIKU_MODEL") or "").strip()
    preferred = [configured_model] if configured_model else list(_DEFAULT_ONBOARDING_MODEL_CANDIDATES)
    available_models = await _fetch_available_anthropic_models(api_key)
    model_candidates = _prioritize_available_models(available_models, preferred)

    resp: httpx.Response | None = None
    last_exc: Exception | None = None
    for idx, model_name in enumerate(model_candidates):
        payload = {
            "model": model_name,
            "max_tokens": 2048,
            "temperature": 0.3,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt_text}],
        }
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.post(
                    _ANTHROPIC_MESSAGES_URL,
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json=payload,
                )
            if resp.status_code == 404 and idx < len(model_candidates) - 1:
                logger.warning(
                    "onboarding_haiku_model_not_found model=%s status=404 body=%s",
                    model_name,
                    (resp.text or "")[:300],
                )
                continue
            resp.raise_for_status()
            break
        except Exception as exc:
            last_exc = exc
            if idx < len(model_candidates) - 1:
                logger.warning("onboarding_haiku_attempt_failed model=%s error=%s", model_name, exc)
                continue
            raise

    if resp is None:
        raise RuntimeError(f"Anthropic call failed without response: {last_exc}")

    data = resp.json()
    text = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            text += block.get("text", "")
    text = text.strip()
    try:
        parsed = _extract_json_payload(text)
    except json.JSONDecodeError as exc:
        logger.warning("onboarding_haiku_json_parse_failed: %s | raw=%s", exc, text[:600])
        await record_ai_module_run(
            db,
            user_id=user_id,
            session_id=session_id,
            module="onboarding_haiku_extract_fallback",
            reason="llm_json_parse_failed",
            extra={"raw_preview": text[:400]},
        )
        parsed = _deterministic_fallback_extract(latest_user_answer)

    await record_ai_module_run(
        db,
        user_id=user_id,
        session_id=session_id,
        module="onboarding_haiku_extract",
        reason=f"action={action}",
        duration_ms=None,
        extra={"phase": parsed.get("phase"), "ready_for_confirmation": parsed.get("ready_for_confirmation")},
    )
    return parsed


async def _apply_payload_to_profiles(
    db: AsyncSession,
    *,
    user_id: UUID,
    payload: dict[str, dict[str, Any]],
) -> None:
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise RuntimeError("User not found.")

    pf = (
        await db.execute(select(PersonalFinanceProfile).where(PersonalFinanceProfile.user_id == user_id))
    ).scalar_one_or_none()
    if pf is None:
        pf = PersonalFinanceProfile(user_id=user_id)
        db.add(pf)

    inv = (
        await db.execute(select(InvestmentProfile).where(InvestmentProfile.user_id == user_id))
    ).scalar_one_or_none()
    if inv is None:
        inv = InvestmentProfile(user_id=user_id)
        db.add(inv)

    risk = (await db.execute(select(RiskProfile).where(RiskProfile.user_id == user_id))).scalar_one_or_none()
    if risk is None:
        risk = RiskProfile(user_id=user_id)
        db.add(risk)

    for k, v in payload.get("onboarding_profile", {}).items():
        setattr(pf, k, v)
    for k, v in payload.get("investment_profile", {}).items():
        setattr(inv, k, v)
    for k, v in payload.get("risk_profile", {}).items():
        setattr(risk, k, v)

    if user.is_onboarding_complete is not True:
        user.is_onboarding_complete = True
    if user.date_of_birth is None:
        user.date_of_birth = date(1990, 1, 1)

    await db.flush()
    await maybe_recalculate_effective_risk(db, user_id, "chat_onboarding_confirm")


async def run_onboarding_turn(
    *,
    db: AsyncSession,
    user_id: UUID,
    session_id: UUID,
    conversation_history: list[dict[str, str]],
    latest_user_answer: str,
    action: str,
    accumulated_values: list[dict[str, Any]] | None = None,
) -> OnboardingTurnResult:
    try:
        parsed = await _call_haiku_extract(
            question_catalog=ONBOARDING_QUESTION_CATALOG,
            conversation_history=conversation_history,
            latest_user_answer=latest_user_answer,
            action=action,
            user_id=user_id,
            session_id=session_id,
            db=db,
            accumulated_values=accumulated_values,
        )
    except Exception as exc:
        logger.warning("onboarding_haiku_call_failed: %s", exc)
        await record_ai_module_run(
            db,
            user_id=user_id,
            session_id=session_id,
            module="onboarding_haiku_extract_fallback",
            reason="llm_call_failed",
            extra={"error": str(exc)},
        )
        parsed = _deterministic_fallback_extract(latest_user_answer, accumulated_values)

    extracted_values = parsed.get("extracted_values") or []

    # Merge: keep all previously accumulated values that the LLM didn't re-extract
    if accumulated_values:
        new_fields = {v.get("field") for v in extracted_values}
        for old in accumulated_values:
            if old.get("field") not in new_fields:
                extracted_values.append(old)

    raw_assumptions = parsed.get("assumptions") or []
    assumptions: list[dict[str, Any]] = []
    for a in raw_assumptions:
        if isinstance(a, dict):
            assumptions.append(a)
        elif isinstance(a, str) and a.strip():
            assumptions.append({"field": "general", "label": "Note", "suggested_value": None, "reason": a.strip()})

    raw_advisories = parsed.get("advisories") or []
    advisories: list[str] = []
    for adv in raw_advisories:
        advisories.append(str(adv).strip() if adv else "")
    next_question = parsed.get("next_question")
    phase = parsed.get("phase") or "collecting"
    ready_for_confirmation = bool(parsed.get("ready_for_confirmation"))

    write_payload = _build_write_payload(extracted_values)
    summary_rows = _summary_rows_from_values(extracted_values)

    committed = False
    if action == "confirm_summary":
        await _apply_payload_to_profiles(db, user_id=user_id, payload=write_payload)
        committed = True
        phase = "completed"
        ready_for_confirmation = False
        next_question = None

    assistant_message = _compose_assistant_message(
        phase=phase,
        guidance=str(parsed.get("assistant_guidance") or ""),
        next_question=next_question,
        assumptions=assumptions,
        advisories=advisories,
    )
    return OnboardingTurnResult(
        assistant_message=assistant_message,
        phase=phase,
        next_question=next_question,
        extracted_values=extracted_values,
        assumptions=assumptions,
        advisories=[str(x) for x in advisories],
        summary_rows=summary_rows,
        ready_for_confirmation=ready_for_confirmation,
        committed=committed,
        write_payload_preview=write_payload,
    )
