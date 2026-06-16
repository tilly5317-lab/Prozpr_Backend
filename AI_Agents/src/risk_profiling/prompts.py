from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from persona import build_system_prompt  # shared PI voice (AI_Agents/src)


class RiskProfileSummary(BaseModel):
    """Schema for the LLM tool call that produces the customer-facing summary.

    Forcing a structured output via ``with_structured_output`` rules out
    markdown-fence wrapping, preamble/postamble leakage, and JSON-shape drift —
    the LLM's tool-call payload is the ``summary`` field, nothing else.
    """

    summary: str = Field(
        ...,
        description=(
            "Customer-facing risk profile paragraph: 4-5 conversational sentences "
            "as a single paragraph, plain English, no bullets, no headers, no "
            "preamble. Speak directly to the customer using 'you' / 'your'."
        ),
    )


_RISK_BODY = (
    "You are writing the customer's risk-profile summary. Explain what the numbers mean in "
    "everyday language; the scores are already computed — your job is narrative.\n"
    "\n"
    "Plain-language translations (use these, never the raw term):\n"
    '  "effective risk score" → "your investing style score"\n'
    '  "risk capacity" → "what your finances can comfortably handle"\n'
    '  "risk willingness" → "how adventurous you want to be with your money"\n'
    '  "OSI / occupational stability" → describe income as steady / variable / unpredictable\n'
    '  "equity_boost" → "you\'re saving well";  "equity_reduce" → "your savings are a bit stretched right now"\n'
    'Never surface: "clamped"/"adjusted"/"overridden", raw numeric scores, or internal field names.\n'
    "\n"
    "Format:\n"
    "- Exactly 4-5 warm, conversational sentences as a SINGLE paragraph. Use 'you'/'your'.\n"
    "- Reference concrete facts (age, approximate savings rate, income type) but never echo raw scores/field names.\n"
    "\n"
    "Content — weave in naturally:\n"
    "1. INVESTING STYLE: name the `risk_profile_category` verbatim (you may bold it), then translate "
    "what it means in plain terms.\n"
    "2. STRENGTHS: concrete advantages — age (younger = more time), strong savings rate (>=20%), "
    "substantial net financial assets, healthy expense coverage (>=3x), manageable debt (<=30%), "
    "property ownership.\n"
    "3. INCOME STABILITY: how steady their income is and what market swings they can absorb.\n"
    "4. GENTLE FLAG (only when relevant): if gap_exceeds_3, or savings_rate_adjustment is "
    "'equity_reduce', or current_debt_percent >= 50% — mention it kindly as a nudge, not a warning.\n"
    "\n"
    "Edge cases: at score extremes (1-2 or 9-10) acknowledge the leaning without making it sound "
    "like a problem; very young (<25) → time is a major advantage; older (>55) → focus on protecting "
    "what they've built; high debt + high willingness → lead with the gentle flag.\n"
    "\n"
    "Input fields (human message, pre-translated): age, effective_risk_score, risk_profile_category "
    "(use VERBATIM), risk_capacity_score, risk_willingness, gap_exceeds_3, osi_category/osi, "
    "savings_rate_pct, savings_rate_adjustment, net_financial_assets_indian, expense_coverage, "
    "current_debt_percent, properties_owned. Never expose raw field names.\n"
    "\n"
    "This is a description of the customer's profile, not personalized investment advice. Don't tell "
    "them what to invest in or promise returns."
)
_SYSTEM = build_system_prompt(_RISK_BODY, format_profile="plain", question_aware=False)

_HUMAN = """Customer Profile Data:
- Age: {age}
- Investing Style Score: {effective_risk_score}/10
- Investing Style Category: {risk_profile_category}
- Financial Comfort Score: {risk_capacity_score}/10
- How adventurous they want to be: {risk_willingness}/10
- Big mismatch between comfort and adventurousness: {gap_exceeds_3}
- Job type: {osi_category}  (income stability: {osi}/1.0 — higher means steadier)
- Savings rate: {savings_rate_pct} (signal: {savings_rate_adjustment})
- Net financial assets: {net_financial_assets_indian}
- Expense coverage: {expense_coverage} of annual expenses (higher means more cushion)
- Debt as % of financial assets: {current_debt_percent}
- Properties owned: {properties_owned}

Write the 4-5 sentence customer-friendly summary now."""

summary_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", _SYSTEM),
        ("human", _HUMAN),
    ]
)
