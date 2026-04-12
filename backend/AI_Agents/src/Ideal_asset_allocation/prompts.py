"""
LangChain prompt templates for the 5-step ideal asset allocation pipeline.

Each step loads its reference .md file as the system prompt.
The human message passes the full accumulated state (inputs + all prior step outputs)
as a JSON string, so every step has complete context.
"""

import json
from pathlib import Path
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda

_REFS = Path(__file__).parent / "references"


def _load(filename: str) -> str:
    content = (_REFS / filename).read_text()
    # Escape curly braces so LangChain doesn't interpret them as template variables.
    # {state_json} is injected separately via format_messages, not via the system prompt.
    return content.replace("{", "{{").replace("}", "}}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _serialize(state: dict) -> str:
    """Serialize state to a compact but readable JSON string."""
    return json.dumps(state, indent=2, default=str)


# ── Step 1: Carve-Outs ───────────────────────────────────────────────────────

_STEP1_SYSTEM = _load("carve-outs.md")

_STEP1_HUMAN = """\
Full client state (inputs only at this stage):

{state_json}

Apply the carve-out rules from the reference. Work through \
Emergency Fund → Short-Term Funds → Negative NFA Carve-Out in order, then compute \
`remaining_investable_corpus`. Use numeric fields from `state_json` exactly as given.

Return ONLY a valid JSON object matching the JSON Output schema in the reference — \
no commentary, no markdown fences.
"""

step1_prompt = ChatPromptTemplate.from_messages([
    ("system", _STEP1_SYSTEM),
    ("human", _STEP1_HUMAN),
])


# ── Step 2: Asset Class Allocation ───────────────────────────────────────────

_STEP2_SYSTEM = _load("asset-class-allocation.md")

_STEP2_HUMAN = """\
Accumulated state (client inputs + Step 1 output):

{state_json}

Determine Equities %, Debt %, and Others % for \
`step1_carve_outs.output.remaining_investable_corpus` (not `total_corpus`). \
Use `effective_risk_score` from the top-level inputs for interpolation when needed.

Follow the full computation sequence in the reference: min/max lookup \
(interpolate if non-integer score) → horizon adjustments → market commentary \
overlay → normalize to 100% → round to whole integers. The three class percentages \
must be whole numbers that sum to exactly 100.

Return ONLY a valid JSON object matching the schema in the reference — \
no commentary, no markdown fences.
"""

step2_prompt = ChatPromptTemplate.from_messages([
    ("system", _STEP2_SYSTEM),
    ("human", _STEP2_HUMAN),
])


# ── Step 3: Subgroup Allocation ───────────────────────────────────────────────

_STEP3_SYSTEM = _load("subgroup-allocation.md")

_STEP3_HUMAN = """\
Accumulated state (client inputs + Steps 1–2 outputs):

{state_json}

Break down `step2_asset_class.output` into 13 subgroup allocations. Every \
subgroup field is a % of `step1_carve_outs.output.remaining_investable_corpus` \
(same basis as Step 2).

Execute Phases A → B exactly as described in the reference.

Before returning, verify these sums use the **rounded** Step 2 percentages from \
`step2_asset_class.output` (equities_pct, debt_pct, others_pct):
- All eight equity subgroup `*_pct` values (including tax_efficient) sum to \
  `equities_pct`.
- All four debt subgroup `*_pct` values sum to `debt_pct`.
- `gold_commodities_pct` equals `others_pct`.
- All thirteen lines together sum to 100%.

If any class total is short or long, adjust within that class (reference: largest \
line-item ±1%) until the three equalities above hold.

Return ONLY a valid JSON object matching the schema in the reference — \
no commentary, no markdown fences.
"""

step3_prompt = ChatPromptTemplate.from_messages([
    ("system", _STEP3_SYSTEM),
    ("human", _STEP3_HUMAN),
])


# ── Step 4: Guardrails validation & correction (LLM) ─────────────────────────
# Runs on every pipeline invocation; the model validates Step 3 and returns
# corrected percentages and rupee amounts per references/guardrails.md.

_STEP4_SYSTEM = _load("guardrails.md")

_STEP4_HUMAN = """\
Accumulated state (client inputs + Steps 1–3 outputs):

{state_json}

Validate `step3_subgroups.output.subgroup_allocation` against all 4 rules in the \
reference. Apply the Violation Resolution Process (Sub-steps 1–5) until every rule \
passes. Compute `rounded_amounts` from \
`step1_carve_outs.output.remaining_investable_corpus`.

**All percentages are % of remaining_investable_corpus (total corpus basis).** \
To check subgroup bounds (which are % of parent), convert first: \
`subgroup_share = subgroup_pct / parent_pct × 100`, then compare to the bounds table.

**ELSS lock:** Do NOT change `tax_efficient_equities_pct` unless it is itself in \
violation of Rule 4. It is always the last subgroup adjusted.

**Consistency requirement:** `validation_results` must reflect the state AFTER all \
corrections. If `output.all_rules_pass` is true, then `rule_1_total_100`, \
`rule_2_subgroups_sum_to_parent`, `rule_3_asset_class_in_range`, and \
`rule_4_subgroup_in_range` must all be true, and `violations_found` must be empty. \
Do not carry forward stale violation entries for issues you already resolved.

Before writing the output JSON, run Sub-step 5 (final verification): show the explicit \
arithmetic for each check. Only set `all_rules_pass: true` if every check passes.

Return ONLY a valid JSON object matching the output schema in the reference — \
no commentary, no markdown fences.
"""

step4_prompt = ChatPromptTemplate.from_messages([
    ("system", _STEP4_SYSTEM),
    ("human", _STEP4_HUMAN),
])


# ── Step 5: Presentation ──────────────────────────────────────────────────────

_STEP5_SYSTEM = (
    _load("presentation.md")
    + "\n\n---\n\n## Mututal Type Reference\n\n"
    + _load("scheme_classification.md")
)

_STEP5_HUMAN = """\
Full pipeline state (client inputs + Steps 1–4 outputs):

{state_json}

Produce the final presentation JSON matching the schema in the reference above.

Data sources (do not recompute allocation percentages; trust Step 4 when present):
- Subgroup % and rupee amounts: `step4_validation.output.validated_allocation` and \
`step4_validation.output.rounded_amounts`
- Carve-outs and remaining corpus: `step1_carve_outs.output`
- `recommended_fund`, `asset_class_subcategory`, and `isin`: copy exactly from the \
scheme_classification table in the system prompt — match on `asset_subgroup`

Verify: `grand_total` equals `total_corpus` (carve-outs plus every allocated subgroup \
amount reconciles to the full corpus).

For the `rationale` object, follow the Rationale Guidelines in the reference. \
Additional emphasis — NEVER use: beta, alpha, duration risk, NAV, asset class, \
volatility, liquidity, corpus, portfolio rebalancing. Be warm and direct.
Example tone: "We've kept aside about 4 months of your household expenses in a safe, \
easy-to-access fund — if anything unexpected comes up, you won't need to touch \
your long-term investments."

Return ONLY a valid JSON object matching the schema — no commentary, no markdown fences.
"""

step5_prompt = ChatPromptTemplate.from_messages([
    ("system", _STEP5_SYSTEM),
    ("human", _STEP5_HUMAN),
])


# ── State serializer runnable ─────────────────────────────────────────────────
# Each prompt receives {state_json}. We bind state_json = serialized full state.

def make_state_serializer(prompt):
    """Wrap a prompt so it receives state_json from the full accumulated dict."""
    def _bind(state: dict):
        return prompt.format_messages(state_json=_serialize(state))
    return RunnableLambda(_bind)


# ── Per-step state slimmers ────────────────────────────────────────────────────

def _slim_for_step2(state: dict) -> dict:
    s1 = state.get("step1_carve_outs", {})
    return {
        **{k: v for k, v in state.items() if not k.startswith("step")},
        "step1_carve_outs": {"output": s1.get("output", {})},
    }


def _slim_for_step3(state: dict) -> dict:
    s1 = state.get("step1_carve_outs", {})
    s2 = state.get("step2_asset_class", {})
    input_fields = [
        "effective_risk_score", "tax_regime", "section_80c_utilized",
        "annual_income", "investment_horizon", "investment_horizon_years",
    ]
    return {
        **{k: state[k] for k in input_fields if k in state},
        "step1_carve_outs": {"output": s1.get("output", {})},
        "step2_asset_class": {"output": s2.get("output", {})},
    }


def _slim_for_step4(state: dict) -> dict:
    s1 = state.get("step1_carve_outs", {})
    s2 = state.get("step2_asset_class", {})
    s3 = state.get("step3_subgroups", {})
    return {
        "effective_risk_score": state.get("effective_risk_score"),
        "step1_carve_outs": {
            "output": {
                "remaining_investable_corpus": s1.get("output", {}).get("remaining_investable_corpus")
            }
        },
        "step2_asset_class": {"output": s2.get("output", {})},
        "step3_subgroups": {"output": s3.get("output", {})},
    }


def _slim_for_step5(state: dict) -> dict:
    s1 = state.get("step1_carve_outs", {})
    s4 = state.get("step4_validation", {})
    input_keys = [
        "age", "occupation_type", "investment_horizon", "investment_goal",
        "effective_risk_score", "total_corpus", "monthly_household_expense",
        "primary_income_from_portfolio", "short_term_expenses",
        "tax_regime", "section_80c_utilized",
    ]
    return {
        **{k: state[k] for k in input_keys if k in state},
        "step1_carve_outs": {"output": s1.get("output", {})},
        "step4_validation": {"output": s4.get("output", {})},
    }
