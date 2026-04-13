"""Heuristic liquidity check for cash-out intents.

Compares the user's saved emergency fund against the inferred withdrawal
amount.  If the fund covers the need (with a 10 % buffer), the chat can
skip the full allocation engine and return a short cash-out checklist.
"""


from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.user import User


@dataclass(frozen=True)
class LiquidityGateResult:
    sufficient_for_quick_cash_out_path: bool
    log_reason: str
    inferred_need_inr: float | None
    emergency_fund_inr: float | None


def _parse_inr_amount(text: str) -> float | None:
    t = text.lower().replace(",", "")
    m = re.search(
        r"(?:₹|rs\.?|inr)\s*([\d.]+)\s*(lac|lakh|l)\b",
        t,
    )
    if m:
        return float(m.group(1)) * 100_000
    m = re.search(r"(?:₹|rs\.?|inr)\s*([\d.]+)\s*k\b", t)
    if m:
        return float(m.group(1)) * 1_000
    m = re.search(r"\b([\d.]+)\s*(lac|lakh|l)\b", t)
    if m:
        return float(m.group(1)) * 100_000
    m = re.search(r"\b([\d.]+)\s*k\b", t)
    if m:
        return float(m.group(1)) * 1_000
    m = re.search(r"(?:₹|rs\.?|inr)\s*([\d]{4,}(?:\.[\d]+)?)\b", t)
    if m:
        return float(m.group(1))
    return None


def assess_liquidity_for_cash_out(user: User, user_question: str) -> LiquidityGateResult:
    """
    Heuristic only — not financial advice. Uses investment_profile emergency fund vs
    parsed withdrawal amount or a multiple of regular outgoings.
    """
    inv = getattr(user, "investment_profile", None)
    emergency = float(getattr(inv, "emergency_fund", None) or 0) if inv else 0.0
    monthly_out = float(getattr(inv, "regular_outgoings", None) or 0) if inv else 0.0

    parsed = _parse_inr_amount(user_question)
    if parsed is not None:
        need = parsed
        reason_base = "parsed_withdrawal_amount"
    elif monthly_out > 0:
        need = monthly_out * 3
        reason_base = "three_months_regular_outgoings_proxy"
    else:
        return LiquidityGateResult(
            sufficient_for_quick_cash_out_path=False,
            log_reason="liquidity_indeterminate_no_amount_no_outgoings_full_pipeline",
            inferred_need_inr=None,
            emergency_fund_inr=emergency or None,
        )

    buffer = need * 1.1
    sufficient = emergency >= buffer if emergency > 0 else False
    log_reason = (
        f"{reason_base}_need_{need:.0f}_emergency_{emergency:.0f}_quick_path"
        if sufficient
        else f"{reason_base}_need_{need:.0f}_emergency_{emergency:.0f}_full_pipeline"
    )
    return LiquidityGateResult(
        sufficient_for_quick_cash_out_path=sufficient,
        log_reason=log_reason,
        inferred_need_inr=need,
        emergency_fund_inr=emergency or None,
    )


def format_quick_cash_out_response(user: User, user_question: str, gate: LiquidityGateResult) -> str:
    first = getattr(user, "first_name", None) or "there"
    return (
        f"Hi {first}, for “{user_question.strip()[:200]}” your **saved emergency fund** looks sufficient "
        "versus the amount we inferred — so here’s a **short cash-out checklist** (no full portfolio engine run):\n\n"
        "- Confirm amount, bank account, and instrument type (equity MF / debt / cash).\n"
        "- Prefer redeeming from **overweight** sleeves first; watch **exit loads**.\n"
        "- Large exits: consider **STP/SWP** instead of one-shot redemptions.\n\n"
        f"_Heuristic check only ({gate.log_reason}); not tax or liquidity advice._"
    )
