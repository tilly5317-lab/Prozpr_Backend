# Category-Aware Additional-Investment Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a customer asks for a fund category ("smallcap only"), name our top-ranked funds in it + the goal-based caveat, and — when an amount is known (current message OR recent history) — run the real deployment and state honestly where that category stands in it.

**Architecture:** Chat-layer only. One new pure helper (`category.py`), the deploy extractor gains a `focus_category` field + conversation history, the handler grows a category branch, and the formatter prompts/fallbacks gain a `category_ask` block. NO engine change, NO DB schema change.

**Tech Stack:** Python 3.12 (`.venv-mac`), pydantic v2, langchain-anthropic (existing `classify_action` helper), pytest (`asyncio_mode=auto`).

**Spec:** `docs/superpowers/specs/2026-07-04-ainv-category-aware-chat-design.md` — read it first; every behavior and status precedence rule lives there.

## Global Constraints

- **Do NOT git commit** — the user commits himself; each task ends at "tests pass" (overrides the template's commit steps).
- NO engine (`AI_Agents/src/additional_investment/`) changes; NO DB schema changes.
- Prompt text counts as code — body prompts get exact text in this plan.
- The regex fallback `parse_deploy_request` stays current-message-only and keeps its 2-tuple signature (public, separately tested).
- All no-category paths must stay byte-identical to today (regression gate: full ainv suite green).
- Test runner: `.venv-mac/bin/python -m pytest` from `Prozpr_Backend/`.
- House test style: fakes/monkeypatch, no real DB, no live LLM in unit tests. Prompt-JUDGMENT rules (salary-not-reused etc.) are verified in the live smoke (Task 6), not unit tests — unit tests verify plumbing.

---

### Task 1: `category.py` — pure category helper

**Files:**
- Create: `app/domains/additional_investment/services/ainv_engine/category.py`
- Test: `app/domains/additional_investment/services/ainv_engine/tests/test_category.py`

**Interfaces:**
- Consumes: `get_fund_ranking()` from `app.domains.rebalancing.services.rebal_engine.fund_rank` (rows carry `.sub_category`, `.asset_subgroup`, `.rank`, `.fund_name`, `.isin`, `.scheme_code`) — same import precedent as the ainv input builder.
- Produces (Task 4 consumes all of these, exact names):
  - `resolve_category(text: str | None) -> str | None` — canonical `sub_category` or None
  - `top_funds_for_category(category: str, n: int = 3) -> list` — ranking rows, rank-ascending
  - `category_subgroup(category: str) -> str | None` — subgroup of the top-ranked fund
  - `category_status(category, *, deficit_facts, buys, exclude_subgroups) -> str` — one of `"not_ranked" | "excluded_by_policy" | "in_plan" | "subgroup_funded_other_funds" | "at_or_above_ideal" | "plan_by_goals"` (last = SIP/no-deficit degraded form)

- [ ] **Step 1: Write the failing tests** — create `tests/test_category.py`:

```python
"""Pure tests for the category helper (fakes only — the ranking loader is
monkeypatched with in-memory rows; no CSV, no DB, no LLM)."""

from dataclasses import dataclass

import pytest

from app.domains.additional_investment.services.ainv_engine import category as cat


@dataclass
class _Row:
    asset_subgroup: str
    sub_category: str
    rank: int
    isin: str
    scheme_code: str
    fund_name: str


_RANKING = {
    "high_beta_equities": [
        _Row("high_beta_equities", "Mid Cap Fund", 1, "INF_M1", "M1", "Mid One"),
        _Row("high_beta_equities", "Small Cap Fund", 2, "INF_S1", "S1", "Small One"),
        _Row("high_beta_equities", "Small Cap Fund", 3, "INF_S2", "S2", "Small Two"),
        _Row("high_beta_equities", "Small Cap Fund", 4, "INF_S3", "S3", "Small Three"),
    ],
    "tax_efficient_equities": [
        _Row("tax_efficient_equities", "ELSS", 1, "INF_E1", "E1", "Tax Saver One"),
    ],
    "multi_asset": [
        _Row("multi_asset", "Multi Asset Allocation", 1, "INF_A1", "A1", "Multi One"),
    ],
}


@pytest.fixture(autouse=True)
def _patch_ranking(monkeypatch):
    monkeypatch.setattr(cat, "get_fund_ranking", lambda: _RANKING)


def test_resolve_direct_and_synonyms():
    assert cat.resolve_category("Small Cap Fund") == "Small Cap Fund"
    assert cat.resolve_category("smallcap") == "Small Cap Fund"
    assert cat.resolve_category("small cap funds") == "Small Cap Fund"
    assert cat.resolve_category("midcap") == "Mid Cap Fund"
    assert cat.resolve_category("elss") == "ELSS"
    assert cat.resolve_category("tax saving") == "ELSS"


def test_resolve_unknown_and_none():
    assert cat.resolve_category("REIT funds") is None
    assert cat.resolve_category(None) is None
    assert cat.resolve_category("   ") is None


def test_top_funds_ordered_by_rank_and_capped():
    funds = cat.top_funds_for_category("Small Cap Fund", n=2)
    assert [f.fund_name for f in funds] == ["Small One", "Small Two"]
    assert len(cat.top_funds_for_category("Small Cap Fund")) == 3


def test_category_subgroup_is_top_ranked_funds_subgroup():
    assert cat.category_subgroup("Small Cap Fund") == "high_beta_equities"
    assert cat.category_subgroup("Unknown Cat") is None


def _buy(sub_category, asset_subgroup, amount=10000.0):
    """Stand-in for engine FundBuy (only the attrs category_status reads)."""
    from types import SimpleNamespace

    return SimpleNamespace(
        sub_category=sub_category, asset_subgroup=asset_subgroup, amount_inr=amount
    )


_EXCLUDE = {"tax_efficient_equities", "non_mf_equities"}


def test_status_precedence_not_ranked_first():
    assert cat.category_status(
        None, deficit_facts=[], buys=[], exclude_subgroups=_EXCLUDE
    ) == "not_ranked"


def test_status_excluded_by_policy():
    assert cat.category_status(
        "ELSS", deficit_facts=[], buys=[], exclude_subgroups=_EXCLUDE
    ) == "excluded_by_policy"


def test_status_in_plan_when_a_buy_matches_category():
    buys = [_buy("Small Cap Fund", "high_beta_equities")]
    assert cat.category_status(
        "Small Cap Fund", deficit_facts=[], buys=buys, exclude_subgroups=_EXCLUDE
    ) == "in_plan"


def test_status_subgroup_funded_via_other_funds():
    buys = [_buy("Mid Cap Fund", "high_beta_equities")]
    facts = [{"subgroup": "high_beta_equities", "ideal_inr": 100000.0,
              "current_inr": 40000.0, "gap_inr": 60000.0, "buy_inr": 10000.0}]
    assert cat.category_status(
        "Small Cap Fund", deficit_facts=facts, buys=buys, exclude_subgroups=_EXCLUDE
    ) == "subgroup_funded_other_funds"


def test_status_at_or_above_ideal_when_no_gap_row():
    # subgroup absent from deficit rows == no gap == nothing deployed there
    assert cat.category_status(
        "Small Cap Fund", deficit_facts=[], buys=[], exclude_subgroups=_EXCLUDE
    ) == "at_or_above_ideal"


def test_status_sip_degrades_to_plan_by_goals():
    # deficit_facts=None (SIP / legacy path) and no matching buy
    assert cat.category_status(
        "Small Cap Fund", deficit_facts=None, buys=[], exclude_subgroups=_EXCLUDE
    ) == "plan_by_goals"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_category.py -q`
Expected: FAIL — `ModuleNotFoundError: ... category`

- [ ] **Step 3: Implement** — create `category.py`:

```python
"""Category resolution + status for the category-aware additional-investment chat.

Pure helpers (no DB, no LLM): canonicalise a customer's free-text category
against the ACTUAL ``sub_category`` values in the fund ranking, list the
top-ranked funds in a category, and decide where the asked category stands in
a computed deployment (spec 2026-07-04). The status vocabulary and its
precedence are contractual — the formatter prompts narrate per-status.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from app.domains.rebalancing.services.rebal_engine.fund_rank import get_fund_ranking

# Free-text synonyms → canonical ranking sub_category. Keys are matched as
# lowercase substrings of the customer's category text (longest key first, so
# "small cap" wins over "cap"). Extend as categories join the ranking.
_CATEGORY_SYNONYMS: dict[str, str] = {
    "small cap": "Small Cap Fund",
    "smallcap": "Small Cap Fund",
    "mid cap": "Mid Cap Fund",
    "midcap": "Mid Cap Fund",
    "large cap": "Large Cap Fund",
    "largecap": "Large Cap Fund",
    "bluechip": "Large Cap Fund",
    "blue chip": "Large Cap Fund",
    "flexi cap": "Flexi Cap Fund",
    "flexicap": "Flexi Cap Fund",
    "multi cap": "Flexi Cap Fund",
    "multicap": "Flexi Cap Fund",
    "elss": "ELSS",
    "tax saving": "ELSS",
    "tax saver": "ELSS",
    "80c": "ELSS",
    "gold": "Gold ETF",
    "arbitrage": "Arbitrage Fund",
    "value fund": "Value Fund",
    "contra": "Contra Fund",
    "multi asset": "Multi Asset Allocation",
    "balanced advantage": "Dynamic Asset Allocation or Balanced Advantage",
    "dynamic asset": "Dynamic Asset Allocation or Balanced Advantage",
    "aggressive hybrid": "Aggressive Hybrid Fund",
    "hybrid": "Aggressive Hybrid Fund",
    "short duration": "Short Duration Fund",
    "debt fund": "Short Duration Fund",
    "international": "FoF Overseas",
    "overseas": "FoF Overseas",
    "us fund": "FoF Overseas",
    "nasdaq": "FoF Overseas",
    "fof": "FoF Domestic",
}


def _ranking_categories() -> set[str]:
    """The sub_category values actually present in the ranking (live, not
    hardcoded — a synonym target absent from the ranking resolves to None)."""
    return {
        row.sub_category
        for rows in get_fund_ranking().values()
        for row in rows
        if row.sub_category
    }


def resolve_category(text: Optional[str]) -> Optional[str]:
    """Canonicalise free text to a ranking sub_category, or None.

    Direct case-insensitive match first, then longest-synonym substring match.
    Only categories that EXIST in the ranking are returned — unknown asks stay
    None so the reply can honestly say "we don't rank funds there".
    """
    if not text or not text.strip():
        return None
    needle = text.strip().lower()
    present = _ranking_categories()
    for canonical in present:
        if canonical.lower() == needle:
            return canonical
    for key in sorted(_CATEGORY_SYNONYMS, key=len, reverse=True):
        # Word-boundary match (task-1 review): raw substring matching wrongly
        # resolved "Goldman Sachs" → gold, "focused fund" → us fund.
        if re.search(rf"\b{re.escape(key)}\b", needle):
            target = _CATEGORY_SYNONYMS[key]
            return target if target in present else None
    return None


def _category_rows(category: str) -> list[Any]:
    rows = [
        row
        for subgroup_rows in get_fund_ranking().values()
        for row in subgroup_rows
        if row.sub_category == category
    ]
    return sorted(rows, key=lambda r: r.rank)


def top_funds_for_category(category: str, n: int = 3) -> list[Any]:
    """Top-N ranking rows in the category, rank-ascending."""
    return _category_rows(category)[:n]


def category_subgroup(category: str) -> Optional[str]:
    """The asset_subgroup the category deploys through — the top-ranked fund's
    subgroup (contractual tie-break for categories spanning subgroups)."""
    rows = _category_rows(category)
    return rows[0].asset_subgroup if rows else None


def category_status(
    category: Optional[str],
    *,
    deficit_facts: Optional[list[dict[str, Any]]],
    buys: list[Any],
    exclude_subgroups: set[str],
) -> str:
    """Where the asked category stands in the computed plan.

    Precedence (first match wins — spec 2026-07-04):
      not_ranked → excluded_by_policy → in_plan → subgroup_funded_other_funds
      → at_or_above_ideal.  ``deficit_facts is None`` (SIP / legacy path) and
      no matching buy → the degraded ``plan_by_goals`` form.
    """
    if category is None:
        return "not_ranked"
    subgroup = category_subgroup(category)
    if subgroup in exclude_subgroups:
        return "excluded_by_policy"
    if any(getattr(b, "sub_category", None) == category for b in buys):
        return "in_plan"
    if deficit_facts is None:
        return "plan_by_goals"
    row = next((r for r in deficit_facts if r.get("subgroup") == subgroup), None)
    if row is not None and float(row.get("buy_inr", 0.0)) > 0:
        return "subgroup_funded_other_funds"
    return "at_or_above_ideal"
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_category.py -q`
Expected: PASS (11 tests)

---

### Task 2: Extractor — `focus_category` + conversation history

**Files:**
- Modify: `app/domains/additional_investment/services/ainv_engine/chat.py` (`_DeployRequest`, `_DEPLOY_EXTRACT_SYSTEM`, `extract_deploy_request`)
- Test: `app/domains/additional_investment/services/ainv_engine/tests/test_chat.py` (append + update 2 existing patches)

**Interfaces:**
- Consumes: `build_history_block(history: list[dict[str, str]] | None) -> str` from `app.domains.ai_engine.common` (already imported nearby — extend the existing import).
- Produces: `extract_deploy_request(question: str, history: list[dict[str, str]] | None = None) -> tuple[float | None, Cadence, str | None]` — the third element is the RAW category text (Task 4 canonicalises it). Regex-fallback path returns `(amount, cadence, None)`.

- [ ] **Step 1: Write the failing tests** — append to `test_chat.py`:

```python
# ── extractor: focus_category + history (spec 2026-07-04) ───────────────────
def test_extract_returns_category_and_includes_history_block():
    from app.domains.additional_investment.services.ainv_engine import chat as ainv_chat

    seen = {}

    async def _fake_classify(**kwargs):
        seen.update(kwargs)
        return ainv_chat._DeployRequest(
            amount_inr=500000.0, cadence="lumpsum", focus_category="smallcap"
        )

    history = [
        {"role": "user", "content": "I am looking to invest around 5 lakhs"},
        {"role": "assistant", "content": "Here's the plan..."},
    ]
    with (
        patch.object(ainv_chat, "classify_action", new=_fake_classify),
        patch.object(ainv_chat, "get_settings"),  # hermetic: no .env dependence
    ):
        amount, cadence, raw = asyncio.run(
            ainv_chat.extract_deploy_request("smallcap funds only", history)
        )

    assert (amount, cadence.value, raw) == (500000.0, "lumpsum", "smallcap")
    assert "Recent Conversation History" in seen["user_block"]
    assert "5 lakhs" in seen["user_block"]
    assert "smallcap funds only" in seen["user_block"]
    assert seen["max_tokens"] == 200


def test_extract_regex_fallback_has_no_category():
    from app.domains.additional_investment.services.ainv_engine import chat as ainv_chat

    async def _boom(**kwargs):
        raise RuntimeError("llm down")

    with (
        patch.object(ainv_chat, "classify_action", new=_boom),
        patch.object(ainv_chat, "get_settings"),  # hermetic: no .env dependence
    ):
        amount, cadence, raw = asyncio.run(
            ainv_chat.extract_deploy_request("invest 50k as lumpsum", None)
        )

    assert amount == 50000.0
    assert raw is None
```

- [ ] **Step 2: Update the 2 existing `extract_deploy_request` patches in `test_chat.py`** to the 3-tuple (they currently return 2-tuples and would break the new `handle()` in Task 4 — do it now so one change covers both tasks):
  - `AsyncMock(return_value=(100000.0, Cadence.LUMPSUM))` → `AsyncMock(return_value=(100000.0, Cadence.LUMPSUM, None))`
  - `AsyncMock(return_value=(0.0, Cadence.LUMPSUM))` → `AsyncMock(return_value=(0.0, Cadence.LUMPSUM, None))`

- [ ] **Step 3: Run to verify failure**

Run: `.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_chat.py -q`
Expected: FAIL — `_DeployRequest` has no field `focus_category`; unpack errors.

- [ ] **Step 4: Implement.** In `chat.py`:

**(a)** Extend the common import: `from app.domains.ai_engine.common import build_history_block, ensure_ai_agents_path, format_inr_indian`.

**(b)** Add the field to `_DeployRequest` (after `cadence`):

```python
    focus_category: Optional[str] = Field(
        default=None,
        description=(
            "The fund CATEGORY the customer is asking to invest in, verbatim-ish "
            "(e.g. 'smallcap', 'gold', 'ELSS'). Set ONLY when the customer is "
            "asking for that category for this money — a passing mention ('I sold "
            "my smallcap fund') is NOT a request. One category only: when several "
            "are named, pick the dominant one. Null when no category is asked."
        ),
    )
```

**(c)** Replace `_DEPLOY_EXTRACT_SYSTEM` entirely:

```python
_DEPLOY_EXTRACT_SYSTEM = """You extract three fields from a customer's request to
invest fresh money: the rupee AMOUNT, whether it is a one-time LUMPSUM or a
monthly SIP, and the fund CATEGORY they are asking for (if any). Indian money
shorthand: k/thousand = x1,000; l/lac/lakh = x100,000; cr/crore = x10,000,000.
Ignore numbers that are durations, counts, or years (e.g. "5 years", "3 funds",
"in 2027") — only the money amount goes in amount_inr. A bare "monthly"
describing salary/income/expenses is NOT a SIP; only a recurring INVESTMENT
plan is.

A Recent Conversation History block may precede the current request. Fields the
CURRENT request omits may be filled from history, with two hard rules:
1. The CURRENT request always wins on conflict.
2. A historical amount qualifies ONLY if the customer stated it as money they
   want to invest/deploy. Salary, income, expenses, goal targets, and
   hypothetical/what-if figures NEVER qualify. When in doubt, amount_inr=null.

Examples (H: = earlier history, C: = current request):
- C: "invest 5L as a lumpsum"                    -> 500000, lumpsum, null
- C: "start a 25k monthly SIP in smallcap"       -> 25000, sip_monthly, "smallcap"
- C: "which gold fund should I buy?"             -> null, lumpsum, "gold"
- C: "I sold my smallcap fund, invest 2L"        -> 200000, lumpsum, null
- H: "I want to invest 5 lakhs" C: "smallcap funds only"
                                                 -> 500000, lumpsum, "smallcap"
- H: "my salary is 2L a month" C: "smallcap funds only"
                                                 -> null, lumpsum, "smallcap"
- H: "I want to invest 5 lakhs" C: "make it 2L, ELSS"
                                                 -> 200000, lumpsum, "ELSS"
"""
```

**(d)** Replace `extract_deploy_request`:

```python
async def extract_deploy_request(
    question: str,
    history: list[dict[str, str]] | None = None,
) -> tuple[float | None, Cadence, str | None]:
    """LLM extraction of (deploy amount INR, cadence, raw category) from free text.

    History-aware (spec 2026-07-04): the last 6 turns ride in the user block so a
    category-only follow-up reuses an amount the customer already stated — but
    only invest-intent amounts qualify (prompt rule; doubt → null → the polite
    re-ask). Falls back to the deterministic regex ``parse_deploy_request``
    (current message only, never a category) when the Haiku call fails.
    """
    history_block = build_history_block(history)
    user_block = (
        (history_block + "\n\n" if history_block else "")
        + f"Customer's current request: {question}"
    )
    try:
        result = await classify_action(
            action_model=_DeployRequest,
            system_prompt=_DEPLOY_EXTRACT_SYSTEM,
            user_block=user_block,
            api_key=get_settings().get_anthropic_additional_investment_key(),
            max_tokens=200,
        )
    except Exception as exc:  # broad, mirroring _detect_rebal_action's call site
        logger.warning("extract_deploy_request failed (%s); using regex fallback", exc)
        amount, cadence = parse_deploy_request(question)
        return amount, cadence, None

    raw_category = (result.focus_category or "").strip() or None
    return result.amount_inr, Cadence(result.cadence), raw_category
```

- [ ] **Step 5: Run to verify pass** (the two new tests pass; existing handler tests still pass because Step 2 already updated their patches — `handle()` still unpacks 2 values until Task 4, so ALSO temporarily expect the two handler tests to fail? No: Step 2's 3-tuples break `handle()`'s 2-unpack. To keep the suite green between tasks, Task 4's `handle()` change is small — if you are executing tasks strictly in order, run ONLY the extractor tests here and accept the 2 handler tests red until Task 4 completes:)

Run: `.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_chat.py -q -k "extract"`
Expected: PASS. (`-k "handle"` tests go red now and green again in Task 4 — noted, intentional, do not chase.)

---

### Task 3: Facts pack, prompts, fallbacks — the `category_ask` block

**Files:**
- Modify: `app/domains/additional_investment/services/ainv_engine/chat.py` (`build_ainv_facts_pack`, `_build_fallback_ainv_brief`, both body prompts, new probe body + probe fallback)
- Test: `app/domains/additional_investment/services/ainv_engine/tests/test_chat.py` (append)

**Interfaces:**
- Consumes: nothing new (pure prompt/facts work).
- Produces (Task 4 consumes):
  - `build_ainv_facts_pack(output, deficit_rows=None, category_ask=None)` — adds `facts["category_ask"]` when provided
  - `_build_fallback_ainv_brief(output, category_ask=None)` — appends the deterministic category line
  - `_AINV_CATEGORY_PROBE_BODY: str` and `_build_fallback_category_probe(category_ask) -> str`
  - `category_ask` dict shape (contractual): `{"asked_text": str, "category": str | None, "status": str, "top_funds": [{"fund": str, "sub_category": str}], "subgroup_ideal_inr": float | None, "subgroup_current_inr": float | None}`

- [ ] **Step 1: Write the failing tests** — append to `test_chat.py`:

```python
# ── category_ask facts + prompts (spec 2026-07-04) ──────────────────────────
def _category_ask(status="in_plan", category="Small Cap Fund"):
    return {
        "asked_text": "smallcap",
        "category": category,
        "status": status,
        "top_funds": [
            {"fund": "Nippon India Small Cap Fund", "sub_category": "Small Cap Fund"},
            {"fund": "Bandhan Small Cap Fund", "sub_category": "Small Cap Fund"},
        ],
        "subgroup_ideal_inr": 100000.0,
        "subgroup_current_inr": 150000.0,
    }


def test_facts_pack_carries_category_ask_when_provided():
    from app.domains.additional_investment.services.ainv_engine.chat import (
        build_ainv_facts_pack,
    )

    facts = build_ainv_facts_pack(_output(), category_ask=_category_ask())
    assert facts["category_ask"]["category"] == "Small Cap Fund"


def test_facts_pack_omits_category_ask_by_default():
    from app.domains.additional_investment.services.ainv_engine.chat import (
        build_ainv_facts_pack,
    )

    assert "category_ask" not in build_ainv_facts_pack(_output())


def test_fallback_brief_appends_category_line():
    from app.domains.additional_investment.services.ainv_engine.chat import (
        _build_fallback_ainv_brief,
    )

    brief = _build_fallback_ainv_brief(_output(), category_ask=_category_ask())
    assert "Nippon India Small Cap Fund" in brief
    assert "recommend the plan above" in brief


def test_fallback_brief_handles_not_ranked_category():
    from app.domains.additional_investment.services.ainv_engine.chat import (
        _build_fallback_ainv_brief,
    )

    ask = _category_ask(status="not_ranked", category=None)
    ask["top_funds"] = []
    brief = _build_fallback_ainv_brief(_output(), category_ask=ask)
    assert "don't have ranked funds" in brief


def test_probe_body_and_fallback_exist():
    from app.domains.additional_investment.services.ainv_engine import chat as chat_mod

    assert "category_ask" in chat_mod._AINV_CATEGORY_PROBE_BODY
    assert "caveat" in chat_mod._AINV_CATEGORY_PROBE_BODY.lower() or \
           "recommend" in chat_mod._AINV_CATEGORY_PROBE_BODY.lower()
    probe = chat_mod._build_fallback_category_probe(_category_ask())
    assert "Nippon India Small Cap Fund" in probe
    assert "how much" in probe.lower()


def test_both_plan_bodies_document_category_ask():
    from app.domains.additional_investment.services.ainv_engine import chat as chat_mod

    assert "category_ask" in chat_mod._AINV_DEFICIT_FORMATTER_BODY
    assert "category_ask" in chat_mod._AINV_FORMATTER_BODY
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_chat.py -q -k "category_ask or fallback_brief or probe_body or plan_bodies"`
Expected: FAIL — unexpected kwarg `category_ask`, missing `_AINV_CATEGORY_PROBE_BODY`.

- [ ] **Step 3: Implement.** Five changes in `chat.py`:

**(a)** `build_ainv_facts_pack` gains the parameter and block (after the `deficit_rows` block):

```python
def build_ainv_facts_pack(
    output: AdditionalInvestmentOutput,
    deficit_rows: list[dict[str, Any]] | None = None,
    category_ask: dict[str, Any] | None = None,
) -> dict[str, Any]:
```
```python
    if category_ask is not None:
        # The customer asked for a specific category — top-ranked picks + where
        # that category stands in this plan (spec 2026-07-04). Status vocabulary
        # is contractual; the body prompt narrates per-status.
        facts["category_ask"] = category_ask
```

**(b)** `_build_fallback_ainv_brief` gains the parameter and appends before returning (both return paths — restructure to build `out` first, append, join):

```python
def _build_fallback_ainv_brief(
    output: AdditionalInvestmentOutput,
    category_ask: dict[str, Any] | None = None,
) -> str:
```
and just before the final `return`, append:
```python
    if category_ask is not None:
        out.append("")
        out.append(_fallback_category_line(category_ask))
```
with the shared line builder (place above `_build_fallback_ainv_brief`):
```python
def _fallback_category_line(category_ask: dict[str, Any]) -> str:
    """Deterministic one-liner so the fallback path never ignores the asked
    category (the original bug). Names the top picks + the standing caveat."""
    asked = category_ask.get("asked_text") or "that category"
    funds = [f["fund"] for f in category_ask.get("top_funds") or []]
    if not funds:
        return (
            f"_On {asked} specifically: we don't have ranked funds in that "
            "category — the plan above follows your goals instead._"
        )
    return (
        f"_On {asked} specifically: our top-ranked picks are "
        f"{', '.join(funds)} — but we recommend the goal-based plan above "
        "over concentrating in one category._"
    )
```
NOTE: the no-buys early-return path (`if not output.buys: return ...`) must ALSO
append the category line — convert that path to use the same `out` list.

**(c)** New probe body (after `_AINV_DEFICIT_FORMATTER_BODY`):

```python
_AINV_CATEGORY_PROBE_BODY = """The customer asked which funds to buy in a specific
CATEGORY but has not yet said how much they want to invest. Answer the literal
question honestly, then ask for the amount. The shared house-style rules above
apply.

FACTS_PACK has a single field, `category_ask`:
  asked_text  — the category in the customer's words (e.g. "smallcap").
  category    — our canonical name for it, or null when we don't rank funds
                in that category.
  status      — "not_ranked" when category is null; otherwise ignore here.
  top_funds   — our top-ranked funds in the category (fund + sub_category).

Reply shape:
1. Name the top_funds VERBATIM as our highest-ranked picks in that category —
   naming them is the point. When top_funds is empty (not_ranked), say plainly
   that we don't have ranked funds in that category and do NOT invent any.
2. ALWAYS the caveat, in PI's voice: putting everything into one category isn't
   what we'd recommend — our plan spreads fresh money across the gaps in their
   goal-based portfolio.
3. Ask how much they want to invest and whether one-time lumpsum or monthly
   SIP — once known, we'll show exactly where the money goes (including their
   category, when it fits).
Length: 3-5 sentences, warm. Do NOT fabricate amounts, returns, or rankings.
"""
```

**(d)** Probe fallback (near the other fallback builders):

```python
def _build_fallback_category_probe(category_ask: dict[str, Any]) -> str:
    """Deterministic case-2 reply when the formatter fails: picks + caveat + ask."""
    asked = category_ask.get("asked_text") or "that category"
    funds = [f["fund"] for f in category_ask.get("top_funds") or []]
    if funds:
        lead = (
            f"Our top-ranked {asked} picks are: " + ", ".join(funds) + ". "
            "That said, we'd recommend spreading fresh money across your "
            "goal-based plan rather than concentrating in one category."
        )
    else:
        lead = (
            f"We don't have ranked funds in {asked} right now — our "
            "recommendations follow your goal-based plan instead."
        )
    return (
        lead
        + " How much would you like to invest, and should it be a one-time "
        "lumpsum or a monthly SIP?"
    )
```

**(e)** Append the same `category_ask` documentation section to BOTH plan body
prompts (`_AINV_DEFICIT_FORMATTER_BODY` and `_AINV_FORMATTER_BODY`), before
their closing `"""`:

```
When FACTS_PACK contains `category_ask`, the customer asked for a specific fund
category — address it EXPLICITLY (never ignore it):
  asked_text / category — their words / our canonical category (null = we don't
           rank funds there: say so plainly, do NOT invent any).
  top_funds — our top-ranked funds in that category; name them VERBATIM.
  status — where the category stands in THIS plan; narrate accordingly:
    in_plan                      — the plan already buys it: point at that buy.
    subgroup_funded_other_funds  — its part of the portfolio received money via
                                   other funds; name the category picks for a
                                   category-specific tilt.
    at_or_above_ideal            — they already hold at/above their ideal there
                                   (subgroup_current_inr vs subgroup_ideal_inr):
                                   say this deployment adds none, name the top
                                   picks anyway, and caution against
                                   overweighting further.
    excluded_by_policy           — we never deploy fresh chat money there (e.g.
                                   ELSS 3-year lock-in): name the picks, state
                                   the policy.
    plan_by_goals                — (SIP) the plan deploys by goals; name the
                                   category picks alongside.
  ALWAYS close the category topic with the caveat: concentrating in one
  category is not what we'd recommend — the plan spreads by their goals.
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_chat.py -q -k "category_ask or fallback_brief or probe_body or plan_bodies"`
Expected: PASS (6 tests)

---

### Task 4: Handler routing + service `focus_category` threading

**Files:**
- Modify: `app/domains/additional_investment/services/ainv_engine/chat.py` (`handle`, `_format_or_fallback_ainv`, new `_build_category_ask`, new import)
- Modify: `app/domains/additional_investment/services/ainv_engine/service.py` (`compute_additional_investment_result` — `focus_category` param → `request_extras`)
- Test: `app/domains/additional_investment/services/ainv_engine/tests/test_chat.py`, `tests/test_service.py` (append)

**Interfaces:**
- Consumes: Task 1's `resolve_category`, `top_funds_for_category`, `category_subgroup`, `category_status`; Task 2's 3-tuple extractor; Task 3's `category_ask` plumbing; `_EXCLUDE_SUBGROUPS` from `ainv_engine.input_builder`.
- Produces: `compute_additional_investment_result(..., focus_category: str | None = None)`; `request_extras` carries `"focus_category"` when set.

- [ ] **Step 1: Write the failing handler tests** — append to `test_chat.py`:

```python
# ── handler routing: category branches (spec 2026-07-04) ────────────────────
def _patch_probe_format():
    """Patch the shared formatter used by the probe path; returns the mock."""
    return patch(
        "app.domains.ai_engine.answer_formatter.formatter.format_answer",
        new=AsyncMock(return_value="probe reply"),
    )


def test_category_without_amount_probes_and_skips_compute():
    from app.domains.additional_investment.services.ainv_engine import chat as ainv_chat

    compute = AsyncMock()
    with (
        patch.object(
            ainv_chat,
            "extract_deploy_request",
            new=AsyncMock(return_value=(None, Cadence.LUMPSUM, "smallcap")),
        ),
        patch.object(ainv_chat, "compute_additional_investment_result", new=compute),
        patch.object(
            ainv_chat, "resolve_category", new=lambda t: "Small Cap Fund"
        ),
        patch.object(
            ainv_chat,
            "top_funds_for_category",
            new=lambda c, n=3: [],
        ),
        # hermetic: _build_category_ask must not read the real ranking CSV
        patch.object(ainv_chat, "category_subgroup", new=lambda c: "high_beta_equities"),
        patch.object(ainv_chat, "category_status", new=lambda *a, **k: "in_plan"),
        _patch_probe_format(),
        patch(
            "app.domains.ai_engine.answer_formatter.formatter.record_ai_module_run",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = asyncio.run(ainv_chat.handle(_ctx("which smallcap fund?")))

    assert result.text == "probe reply"
    compute.assert_not_awaited()


def test_category_with_amount_computes_and_passes_focus_category():
    from app.domains.additional_investment.services.ainv_engine import chat as ainv_chat
    from app.domains.additional_investment.services.ainv_engine.service import (
        AdditionalInvestmentRunOutcome,
    )

    outcome = AdditionalInvestmentRunOutcome(output=_output(), run_id=None)
    compute = AsyncMock(return_value=outcome)
    seen_facts = {}

    async def _fake_format(**kwargs):
        seen_facts.update(kwargs.get("facts_pack") or {})
        return "plan + category reply"

    with (
        patch.object(
            ainv_chat,
            "extract_deploy_request",
            new=AsyncMock(return_value=(500000.0, Cadence.LUMPSUM, "smallcap")),
        ),
        patch.object(ainv_chat, "compute_additional_investment_result", new=compute),
        patch.object(ainv_chat, "resolve_category", new=lambda t: "Small Cap Fund"),
        patch.object(ainv_chat, "top_funds_for_category", new=lambda c, n=3: []),
        patch.object(ainv_chat, "category_subgroup", new=lambda c: "high_beta_equities"),
        # hermetic: category_status internally reads the ranking CSV — patch it
        patch.object(ainv_chat, "category_status", new=lambda *a, **k: "in_plan"),
        patch.object(ainv_chat, "format_with_telemetry", new=AsyncMock(side_effect=_fake_format)),
    ):
        result = asyncio.run(ainv_chat.handle(_ctx("5L in smallcap")))

    assert result.text == "plan + category reply"
    assert compute.await_args.kwargs["focus_category"] == "Small Cap Fund"
    assert seen_facts["category_ask"]["category"] == "Small Cap Fund"


def test_no_category_paths_unchanged():
    """raw_category None → the pre-existing ask-amount flow, byte-identical."""
    from app.domains.additional_investment.services.ainv_engine import chat as ainv_chat

    compute = AsyncMock()
    with (
        patch.object(
            ainv_chat,
            "extract_deploy_request",
            new=AsyncMock(return_value=(None, Cadence.LUMPSUM, None)),
        ),
        patch.object(ainv_chat, "compute_additional_investment_result", new=compute),
        patch.object(
            ainv_chat,
            "format_relay_or_canned",
            new=AsyncMock(return_value="how much, and lumpsum or SIP?"),
        ),
    ):
        result = asyncio.run(ainv_chat.handle(_ctx("invest")))

    assert result.text == "how much, and lumpsum or SIP?"
    compute.assert_not_awaited()
```

- [ ] **Step 2: Write the failing service test** — append to `test_service.py`:

```python
@pytest.mark.asyncio
async def test_focus_category_lands_in_request_extras(monkeypatch):
    from additional_investment.models import Cadence
    from app.domains.additional_investment.services.ainv_engine import service as svc

    seen = {}

    async def _fake_persist(db, uid, output, **kwargs):
        seen.update(kwargs)
        return uuid.uuid4()

    with patch.object(
        svc, "load_holdings_snapshot", new=_empty_snapshot_mock()
    ), patch.object(
        svc, "compute_practical_allocation_result",
        new=AsyncMock(return_value=_fake_alloc()),
    ), patch.object(
        svc, "build_additional_investment_input_for_user",
        new=AsyncMock(return_value=(_fake_ainv_input(500000.0), {})),
    ), patch.object(
        svc, "persist_practical_allocation_run",
        new=AsyncMock(return_value=uuid.uuid4()),
    ), patch.object(
        svc, "persist_additional_investment_recommendation", new=_fake_persist,
    ):
        await svc.compute_additional_investment_result(
            SimpleNamespace(id=uuid.uuid4()),
            "5L in smallcap",
            db=SimpleNamespace(),
            acting_user_id=uuid.uuid4(),
            chat_session_id=uuid.uuid4(),
            deploy_amount_inr=500000.0,
            cadence=Cadence.LUMPSUM,
            chat_ctx=SimpleNamespace(),
            persist=True,
            focus_category="Small Cap Fund",
        )

    assert seen["request_extras"]["focus_category"] == "Small Cap Fund"
    assert seen["request_extras"]["deployment_mode"] == "deficit_fill"
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_chat.py app/domains/additional_investment/services/ainv_engine/tests/test_service.py -q`
Expected: FAIL — `handle()` unpacks 2 values; unknown kwarg `focus_category`.

- [ ] **Step 4: Implement the service change** — in `service.py`, add the parameter after `persist`:

```python
    persist: bool = False,
    focus_category: Optional[str] = None,
```

and replace the `request_extras=(...)` block in the persist call with a
prebuilt dict (place just above the `if persist and chat_session_id` block):

```python
    # Mode + category metadata merged over the engine-input dump at persist
    # time (spec 2026-07-03 / 2026-07-04). None when there is nothing to add.
    request_extras: Optional[dict] = None
    _extras: dict = {}
    if snapshot is not None:
        _extras["deployment_mode"] = "deficit_fill"
        _extras["base_corpus_inr"] = snapshot.total_inr
    if focus_category:
        _extras["focus_category"] = focus_category
    if _extras:
        request_extras = _extras
```

and in the persist call: `request_extras=request_extras,`.

- [ ] **Step 5: Implement the handler** — in `chat.py`:

**(a)** New import (top, with the other ainv_engine imports):

```python
from app.domains.additional_investment.services.ainv_engine.category import (
    category_status,
    category_subgroup,
    resolve_category,
    top_funds_for_category,
)
from app.domains.additional_investment.services.ainv_engine.input_builder import (
    _EXCLUDE_SUBGROUPS,
)
```

**(b)** The `category_ask` builder (above `handle`):

```python
def _build_category_ask(
    raw_category: str,
    category: str | None,
    deficit_facts: list[dict[str, Any]] | None,
    buys: list[Any],
) -> dict[str, Any]:
    """Assemble the contractual category_ask block (spec 2026-07-04)."""
    top = top_funds_for_category(category) if category else []
    status = category_status(
        category,
        deficit_facts=deficit_facts,
        buys=buys,
        exclude_subgroups=set(_EXCLUDE_SUBGROUPS),
    )
    subgroup = category_subgroup(category) if category else None
    row = next(
        (r for r in (deficit_facts or []) if r.get("subgroup") == subgroup), None
    )
    return {
        "asked_text": raw_category,
        "category": category,
        "status": status,
        "top_funds": [
            {"fund": r.fund_name, "sub_category": r.sub_category} for r in top
        ],
        "subgroup_ideal_inr": row.get("ideal_inr") if row else None,
        "subgroup_current_inr": row.get("current_inr") if row else None,
    }
```

**(c)** The probe formatter (above `handle`):

```python
async def _format_category_probe(
    ctx: TurnContext, category_ask: dict[str, Any]
) -> str:
    """Case 2 (category, no amount): picks + caveat + ask, via the shared
    formatter; deterministic probe brief on failure."""
    return await format_with_telemetry(
        ctx=ctx,
        facts_pack={"category_ask": category_ask},
        body_prompt=_AINV_CATEGORY_PROBE_BODY,
        module_name="additional_investment",
        action_mode="category_probe",
        profile={"first_name": getattr(ctx.user_ctx, "first_name", None)},
        build_fallback=lambda: _build_fallback_category_probe(category_ask),
    )
```

**(d)** `_format_or_fallback_ainv` gains + threads `category_ask`:

```python
async def _format_or_fallback_ainv(
    ctx: TurnContext,
    output: AdditionalInvestmentOutput,
    deficit_facts: list[dict[str, Any]] | None = None,
    category_ask: dict[str, Any] | None = None,
) -> str:
```
with `facts_pack=build_ainv_facts_pack(output, deficit_rows=deficit_facts, category_ask=category_ask)` and `build_fallback=lambda: _build_fallback_ainv_brief(output, category_ask=category_ask)`.

**(e)** `handle()` — replace the extraction + amount-gate block:

```python
    amount, cadence, raw_category = await extract_deploy_request(
        ctx.user_question, ctx.conversation_history
    )
    category = resolve_category(raw_category) if raw_category else None

    if amount is None or amount <= 0:
        if raw_category is not None:
            # Case 2 (spec 2026-07-04): answer the category question honestly,
            # then ask for the amount — never a dead end, never a hallucinated
            # capability.
            category_ask = _build_category_ask(raw_category, category, None, [])
            text = await _format_category_probe(ctx, category_ask)
            return ChatHandlerResult(text=text)
        text = await format_relay_or_canned(
            ctx=ctx,
            module_name="additional_investment",
            message=_MSG_ASK_AMOUNT,
        )
        return ChatHandlerResult(text=text)
```

then thread the category through compute + formatting — the compute call keeps
every kwarg it has today and gains ONE line (`focus_category=category`):

```python
    outcome = await compute_additional_investment_result(
        ctx.user_ctx,
        ctx.user_question,
        db=ctx.db,
        acting_user_id=ctx.effective_user_id,
        chat_session_id=ctx.session_id,
        deploy_amount_inr=amount,
        cadence=cadence,
        chat_ctx=ctx,
        persist=True,  # Plan 3b: persist the recommendation row
        focus_category=category,
    )
```
```python
    category_ask = (
        _build_category_ask(
            raw_category, category, outcome.deficit_facts, outcome.output.buys
        )
        if raw_category is not None
        else None
    )
    text = await _format_or_fallback_ainv(
        ctx,
        outcome.output,
        deficit_facts=outcome.deficit_facts,
        category_ask=category_ask,
    )
```

(The `blocking_message` relay in between stays untouched.)

- [ ] **Step 6: Run the whole ainv domain**

Run: `.venv-mac/bin/python -m pytest app/domains/additional_investment -q`
Expected: ALL PASS — including the two handler tests that went red in Task 2.

---

### Task 5: Full regression

**Files:** none (verification only)

- [ ] **Step 1:**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/additional_investment/Testing/ app/domains/additional_investment app/domains/practical_asset_allocation -q`
Expected: ALL PASS (engine untouched — its suite must be bit-identical).

- [ ] **Step 2: Spec checklist walk** — tick every spec section against the code: four cases ✓, five statuses + SIP degraded ✓ with precedence ✓, history rules + safe-null ✓, synonym map against live ranking ✓, probe body + both plan bodies ✓, both fallbacks carry the category ✓, `focus_category` in request_extras ✓, regex fallback unchanged ✓. Report any unticked line before Task 6.

---

### Task 6: Live smoke — replay the original bug transcript

**Files:**
- Create: `/Users/Amoul/.claude/jobs/cc096a6e/tmp/smoke_category.py` (scratch, not repo)

Prereqs: fresh uvicorn on :8001 (`.venv-mac/bin/python -m uvicorn main:app --port 8001`), smoke user `5550000009` / `Test@1234` (see memory `reference_dev_smoke_env`).

- [ ] **Step 1: Write the driver** — same shape as the deficit-fill smoke (login → session → turns → dump replies), with THIS question sequence:

```python
QUESTIONS = [
    # the original 2026-07-02 bug transcript, verbatim spirit:
    "Which mutual funds should I buy, I am looking for a smallcap fund",   # case 2: picks + caveat + ask
    "I am looking to invest around 5 lakhs as a one time investment",      # case 1: plan + smallcap status
    "since these are not just smallcap I am looking for smallcap funds only",  # history reuse: 5L + category
    # adversarial probes:
    "my salary is 2 lakhs a month",                                        # plants a non-invest figure
    "gold funds only",                                                     # must NOT reuse the salary 2L
    "I want to invest 1 lakh in ELSS",                                     # excluded_by_policy state
    "invest 50000 in REIT funds",                                          # not_ranked state
]
```

- [ ] **Step 2: Run and audit each reply against the spec:**
  - T1 names Nippon/Bandhan/Sundaram + caveat + asks amount (NO hallucinated "let me fetch")
  - T2 runs the plan AND addresses smallcap with its true status (this user is at/above ideal in equity → expect the `at_or_above_ideal` story)
  - T3 reuses ₹5L from history → full case-1 reply, no re-ask ← **the original bug, fixed**
  - T5 must ASK for the amount (salary ₹2L NOT reused) ← Finding-1 guardrail, live
  - T6 names ELSS picks + states the lock-in policy, plan buys none
  - T7 says we don't rank REIT funds, no invented funds
  - DB: latest runs' `request_input.focus_category` present on T3/T6-style runs; log window clean.

- [ ] **Step 3: Stop the server** (`lsof -ti:8001 | xargs kill`) and report the transcript verbatim with pass/fail per expectation.

---

## Deferred / explicitly NOT in this plan

- Multi-category asks (v1 = dominant category, extractor prompt rule).
- Durable category preferences on the profile.
- CLAUDE.md refresh (user-deferred; includes the 3 deficit-fill files + these additions).
