# Additional-Investment Classifier Intent Implementation Plan (Plan 2 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new `additional_investment` intent to the intent classifier and re-adjudicate the prompt boundaries so "deploy this money / which funds" routes to it, while swaps stay in `rebalancing`, target-mix questions stay in `asset_allocation`, and feasibility stays in `goal_planning`.

**Architecture:** Two layers. (1) A deterministic schema change — add the enum member + `_IntentLiteral` string, gated by the existing enum↔Literal drift test. (2) A prompt change — add the intent definition and move the boundary examples — verified by live-LLM boundary-lock tests modeled on the existing `test_classifier_rebalancing.py`.

**Tech Stack:** Python 3.12 (`.venv-mac`), langchain-anthropic + Claude Haiku (`claude-haiku-4-5-20251001`), pydantic, pytest.

## Global Constraints

- **Plan 2 is classifier-only.** Do NOT touch `app/domains/ai_engine/services/flow.py` or add a chat handler — that is Plan 3. Consequence: after Plan 2, the classifier can RETURN `additional_investment`, but no flow/handler exists for it yet; an end-to-end chat asking an additional_investment question will not be handled until Plan 3. This is expected on the feature branch.
- **`Testing/` is gitignored** (`.gitignore:104` → `/AI_Agents/src/*/Testing/`). The new live-test file under `AI_Agents/src/intent_classifier/Testing/` will NOT be committed — expected, matches every module. **Commit source only** (`models.py`, `classifier.py`, `prompts.py`).
- **The deterministic gate is the drift test:** `.venv-mac/bin/python -m pytest app/domains/ai_engine/tests/test_intent_classifier_schema.py::test_intent_literal_matches_enum -v` (and the parallel `AI_Agents/tests/test_intent_classifier.py::TestIntentSchemaDrift`). `_IntentLiteral` MUST list every `Intent` value or these fail.
- **The routing gate is live-LLM and needs `ANTHROPIC_API_KEY`.** The boundary-lock tests call Claude Haiku; without the key they SKIP ("ANTHROPIC_API_KEY not set"). If the key is unavailable in the execution environment, Task 2's prompt change cannot be auto-verified — flag it and treat verification as a manual/keyed follow-up; do NOT mark Task 2 complete on skipped tests without saying so.
- **LLM calls go through LangChain** (`langchain-anthropic`), per repo convention — no change to the call mechanism here.
- **Run all commands from the repo root** `Prozpr_Backend/`. Commit messages end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## File Structure

- `AI_Agents/src/intent_classifier/models.py` — add `ADDITIONAL_INVESTMENT` enum member (source, committed).
- `AI_Agents/src/intent_classifier/classifier.py` — add `"additional_investment"` to `_IntentLiteral`; update the `IntentClassifier` docstring (source, committed).
- `AI_Agents/src/intent_classifier/prompts.py` — add `### 7. additional_investment` section, re-adjudicate `### 1. asset_allocation` and `### 6. rebalancing`, renumber `### 7. out_of_scope` → `### 8.` (source, committed).
- `AI_Agents/src/intent_classifier/Testing/test_classifier_additional_investment.py` — live boundary-lock tests (gitignored, local-only).

---

### Task 1: Add the intent to the schema (enum + Literal + docstring)

**Files:**
- Modify: `AI_Agents/src/intent_classifier/models.py:7-14`
- Modify: `AI_Agents/src/intent_classifier/classifier.py:29-37` and `:133-149`

**Interfaces:**
- Produces: `Intent.ADDITIONAL_INVESTMENT` (value `"additional_investment"`) and the matching `_IntentLiteral` entry. Consumed by Task 2's tests and by Plan 3's flow wiring.

- [ ] **Step 1: Run the drift test to confirm current GREEN baseline**

Run: `.venv-mac/bin/python -m pytest app/domains/ai_engine/tests/test_intent_classifier_schema.py::test_intent_literal_matches_enum -v`
Expected: PASS (enum and Literal currently match with 7 intents).

- [ ] **Step 2: Add the enum member**

In `AI_Agents/src/intent_classifier/models.py`, change the `Intent` enum from:
```python
class Intent(str, Enum):
    ASSET_ALLOCATION = "asset_allocation"
    GOAL_PLANNING = "goal_planning"
    STOCK_ADVICE = "stock_advice"
    PORTFOLIO_QUERY = "portfolio_query"
    GENERAL_MARKET_QUERY = "general_market_query"
    REBALANCING = "rebalancing"
    OUT_OF_SCOPE = "out_of_scope"
```
to (add `ADDITIONAL_INVESTMENT` after `REBALANCING`):
```python
class Intent(str, Enum):
    ASSET_ALLOCATION = "asset_allocation"
    GOAL_PLANNING = "goal_planning"
    STOCK_ADVICE = "stock_advice"
    PORTFOLIO_QUERY = "portfolio_query"
    GENERAL_MARKET_QUERY = "general_market_query"
    REBALANCING = "rebalancing"
    ADDITIONAL_INVESTMENT = "additional_investment"
    OUT_OF_SCOPE = "out_of_scope"
```

- [ ] **Step 3: Run the drift test to confirm it now FAILS (RED)**

Run: `.venv-mac/bin/python -m pytest app/domains/ai_engine/tests/test_intent_classifier_schema.py::test_intent_literal_matches_enum -v`
Expected: FAIL — the enum now has `additional_investment` but `_IntentLiteral` does not, so `set(get_args(_IntentLiteral)) != {i.value for i in Intent}`. This proves the test guards the invariant.

- [ ] **Step 4: Add the matching Literal entry**

In `AI_Agents/src/intent_classifier/classifier.py`, change `_IntentLiteral` from:
```python
_IntentLiteral = Literal[
    "asset_allocation",
    "goal_planning",
    "stock_advice",
    "portfolio_query",
    "general_market_query",
    "rebalancing",
    "out_of_scope",
]
```
to:
```python
_IntentLiteral = Literal[
    "asset_allocation",
    "goal_planning",
    "stock_advice",
    "portfolio_query",
    "general_market_query",
    "rebalancing",
    "additional_investment",
    "out_of_scope",
]
```

- [ ] **Step 5: Update the `IntentClassifier` docstring (keep it accurate)**

In `AI_Agents/src/intent_classifier/classifier.py`, change the docstring (currently "one of seven intents") from:
```python
    Classifies a customer's financial question into one of seven intents:
      - asset_allocation
      - goal_planning
      - stock_advice   (redirects to mutual funds)
      - portfolio_query
      - general_market_query
      - rebalancing   (named-fund swaps / specific fund picks)
      - out_of_scope
```
to:
```python
    Classifies a customer's financial question into one of eight intents:
      - asset_allocation
      - goal_planning
      - stock_advice   (redirects to mutual funds)
      - portfolio_query
      - general_market_query
      - rebalancing   (named-fund swaps / drift trades on existing holdings)
      - additional_investment   (deploy new money / fresh fund selection, BUY-only)
      - out_of_scope
```

- [ ] **Step 6: Run both drift tests to confirm GREEN**

Run: `.venv-mac/bin/python -m pytest app/domains/ai_engine/tests/test_intent_classifier_schema.py AI_Agents/tests/test_intent_classifier.py -v`
Expected: PASS — enum↔Literal back in sync (now 8 intents); `OutOfScopeSubreason` drift test unaffected.

- [ ] **Step 7: Commit (source only)**

```bash
git add AI_Agents/src/intent_classifier/models.py AI_Agents/src/intent_classifier/classifier.py
git commit -m "feat(intent_classifier): add additional_investment intent to schema"
```

---

### Task 2: Prompt re-adjudication + live boundary-lock tests

**Files:**
- Create: `AI_Agents/src/intent_classifier/Testing/test_classifier_additional_investment.py` (gitignored — local-only)
- Modify: `AI_Agents/src/intent_classifier/prompts.py` (the `SYSTEM_PROMPT` string: add §7, edit §1 + §6, renumber §7→§8)

**Interfaces:**
- Consumes: `Intent.ADDITIONAL_INVESTMENT` (Task 1), `IntentClassifier`, `ClassificationInput` (existing).
- Produces: a prompt under which the classifier routes deployment/fund-selection questions to `additional_investment`.

**TDD order:** write the live tests first (they FAIL because the current prompt routes these to asset_allocation/rebalancing), then change the prompt to make them pass. Requires `ANTHROPIC_API_KEY`.

- [ ] **Step 1: Write the live boundary-lock tests (new-intent + regression cases)**

Create `AI_Agents/src/intent_classifier/Testing/test_classifier_additional_investment.py`:
```python
import os

import pytest

from intent_classifier.classifier import IntentClassifier
from intent_classifier.models import ClassificationInput, Intent

pytestmark = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set; skipping live classifier call",
)


def _classify(question: str) -> Intent:
    return IntentClassifier().classify(
        ClassificationInput(customer_question=question)
    ).intent


# --- Should route to additional_investment (new money / fund selection) ---
@pytest.mark.parametrize("question", [
    "I have 5 lakhs to invest, which funds should I buy?",
    "I have 5 lakhs to invest, where should I put it?",
    "I want to start a SIP of 50,000 a month, where should it go?",
    "Which large-cap fund should I invest in?",
    "Which mutual fund is best for me?",
    "I just got a bonus of 2 lakhs, where do I invest it?",
])
def test_routes_to_additional_investment(question: str) -> None:
    got = _classify(question)
    assert got == Intent.ADDITIONAL_INVESTMENT, f"{question!r} -> {got}"


# --- Regression: swaps & drift stay in rebalancing ---
@pytest.mark.parametrize("question", [
    "Should I switch from Axis Bluechip to Mirae Asset Large Cap?",
    "Should I rebalance my portfolio?",
    "I'm overweight in small caps, what should I do?",
])
def test_rebalancing_unchanged(question: str) -> None:
    got = _classify(question)
    assert got == Intent.REBALANCING, f"{question!r} -> {got}"


# --- Regression: target-mix / policy stays in asset_allocation ---
@pytest.mark.parametrize("question", [
    "Is my portfolio aligned with my goals?",
    "Should I add midcap to my portfolio?",
    "Is my current allocation right for my retirement plan?",
])
def test_asset_allocation_unchanged(question: str) -> None:
    got = _classify(question)
    assert got == Intent.ASSET_ALLOCATION, f"{question!r} -> {got}"


# --- Regression: feasibility stays in goal_planning ---
@pytest.mark.parametrize("question", [
    "At my current 50k a month SIP, will I have 2 crore by 2040?",
    "Am I on track for my goals?",
])
def test_goal_planning_unchanged(question: str) -> None:
    got = _classify(question)
    assert got == Intent.GOAL_PLANNING, f"{question!r} -> {got}"
```

- [ ] **Step 2: Run the tests to confirm RED (with the key set)**

Run: `ANTHROPIC_API_KEY=<key> .venv-mac/bin/python -m pytest AI_Agents/src/intent_classifier/Testing/test_classifier_additional_investment.py -v`
Expected: the `test_routes_to_additional_investment` cases FAIL (the current prompt has no `additional_investment` intent, so these route to `asset_allocation` or `rebalancing`). The three regression groups should already PASS. (If no key is available, the tests SKIP — note this and proceed, but Task 2 is then UNVERIFIED; see Global Constraints.)

- [ ] **Step 3: Add the `### 7. additional_investment` section to the prompt**

In `AI_Agents/src/intent_classifier/prompts.py`, immediately AFTER the `### 6. rebalancing` section and its trailing `---`, and BEFORE `### 7. out_of_scope`, insert:

```
### 7. additional_investment
The customer has **money to deploy right now** — a specific lumpsum or a SIP amount — and wants to know **where to put it / which funds to buy**. This is the "put my new money to work" intent: deploying a stated amount and/or selecting specific funds for fresh money. The hallmark is **new money + a deployment or fund-selection ask**, answered as fresh BUYs (never selling existing holdings).

Triggers when the customer is asking:
- Where to invest a specific amount they have or are adding ("I have ₹5L — where should I invest it?", "I got a bonus, where do I put it?")
- How to deploy a SIP / monthly investment ("I want to start a SIP of ₹50k/month — where?", "split my ₹30k SIP across funds")
- Which specific fund(s) to buy with new money ("which large-cap fund should I invest in?", "which mutual fund is best for me?", "recommend a fund for my ₹2L")
- A fund pick at the specific-fund level for NEW money (this is what distinguishes it from asset_allocation's mix-level answer)

Example questions:
- "I have ₹5L to invest — which funds should I buy?"
- "I have ₹5L — where should I invest it?"
- "I want to do a SIP of ₹50,000 a month — where should it go?"
- "Which large-cap fund should I invest in?"
- "Which mutual fund is best for me?"

Key distinction from `asset_allocation`: asset_allocation answers the **target mix** as a policy question ("what should my equity/debt split be?", "is my allocation right?", "should I add midcap?") — it does NOT name funds and does NOT require a specific new amount. additional_investment answers **"deploy THIS money / which funds"** — it always involves new money to put to work and/or specific fund names. If the customer states an amount to invest, or asks which fund to buy, it is additional_investment.

Key distinction from `rebalancing`: rebalancing **moves existing money** to fix drift — it buys AND sells, with tax-aware sequencing ("rebalance my portfolio", "switch from Axis to Mirae", "I'm overweight small caps"). additional_investment only **adds new money** (BUY-only); it never sells. A fund-to-fund **swap** of existing holdings is rebalancing; picking a fund for **new** money is additional_investment.

Key distinction from `goal_planning`: goal_planning answers **feasibility** ("at ₹50k/month, can I hit ₹2cr by 2040?", "am I on track?"). additional_investment answers **where to deploy**. If a question pairs feasibility with deployment ("can I hit ₹10cr AND where do I invest?"), feasibility leads → goal_planning. A pure "where do I invest this ₹X / SIP" with no feasibility ask is additional_investment.

---
```

- [ ] **Step 4: Renumber the out_of_scope heading**

In `prompts.py`, change the heading `### 7. out_of_scope` to `### 8. out_of_scope`. (Only the heading number changes; its body is unchanged. No other line references the number "7" for out_of_scope.)

- [ ] **Step 5: Re-adjudicate `### 1. asset_allocation` (remove deployment/fund-pick items)**

In `prompts.py` §1, make the following edits so asset_allocation is the **mix/policy** intent and deployment/fund-picks point to additional_investment:

(a) Remove the deployment/SIP triggers. Delete these two bullet lines:
```
- Adding a specific amount of their own money to an investment (e.g. "I have ₹5L…")
```
```
- SIP-amount decisions for new investments at the asset-class / sub-category level (NOT specific fund picks)
```

(b) Replace the example bullet:
```
- "I have ₹5L to invest — where should I put it?"
```
with:
```
- "Should I be more aggressive given my age?"
```

(c) In the `**Goal-mention does not flip intent.**` block, replace the example line:
```
- "I have ₹50k/month and want ₹10 crore in 15 years — where should I invest?" → `asset_allocation` (primary ask is allocation; goal is context)
```
with:
```
- "I have ₹50k/month and want ₹10 crore in 15 years — where should I invest?" → `additional_investment` (a stated amount to deploy; goal is context, no feasibility ask)
```

(d) Replace the WHOLE `**Not asset_allocation — these go to `rebalancing`:**` block (its header line plus the three bullets under it) with this corrected block — the swap stays in rebalancing; the two fund-pick lines now route to additional_investment, plus a deployment example:
```
**Not asset_allocation:**
- "Should I switch from Axis Bluechip to Mirae Asset Large Cap?" → `rebalancing` (named fund-to-fund swap of existing holdings)
- "Which large-cap fund should I invest in?" → `additional_investment` (specific fund pick for new money)
- "Which mutual fund is best for me?" → `additional_investment` (fund selection for new money)
- "I have ₹5L — where should I invest it?" → `additional_investment` (a specific new amount to deploy)
```

(e) Update the §1 opening sentence. Change `Specific named-fund swaps and individual fund picks belong to `rebalancing`, not asset_allocation.` to `Specific named-fund swaps belong to `rebalancing`; picking specific funds for new money belongs to `additional_investment`; neither is asset_allocation.`

- [ ] **Step 6: Re-adjudicate `### 6. rebalancing` (fund-picks for new money move out)**

In `prompts.py` §6, make these edits so rebalancing keeps **swaps + drift** but releases **fresh fund-selection**:

(a) Delete this trigger bullet (it moves to additional_investment):
```
- Picking a specific fund within an asset class or sub-category ("which large-cap fund should I pick?")
```

(b) Delete these two example bullets:
```
- "Which large-cap fund should I pick?"
```
```
- "Which mutual fund is best for me?"
```

(c) In the `Key distinction from asset_allocation:` block at the end of §6, append one bullet:
```
- A fund pick for **new** money ("which large-cap fund should I invest in?", "which fund is best for me?") is `additional_investment`, not rebalancing — rebalancing is for **switching/trimming existing** holdings.
```

- [ ] **Step 7: Run the drift test (prompt edits must not break schema)**

Run: `.venv-mac/bin/python -m pytest app/domains/ai_engine/tests/test_intent_classifier_schema.py -v`
Expected: PASS (prompt is a string; schema unaffected — this is a sanity check that nothing structural broke).

- [ ] **Step 8: Run the live boundary-lock tests to confirm GREEN**

Run: `ANTHROPIC_API_KEY=<key> .venv-mac/bin/python -m pytest AI_Agents/src/intent_classifier/Testing/test_classifier_additional_investment.py -v`
Expected: all groups PASS — the 6 additional_investment cases now route correctly, and the rebalancing/asset_allocation/goal_planning regression groups still pass. If any additional_investment case still misroutes, tighten the §7 wording or the §1/§6 "Not …" cross-references and re-run (this is the expected prompt-tuning loop). If the key is unavailable, report the tests SKIPPED and that the prompt change is unverified — do not claim GREEN.

- [ ] **Step 9: Commit (source only — prompt + the schema already committed in Task 1)**

```bash
# Testing/ is gitignored — the test file stays local. Commit the prompt only.
git add AI_Agents/src/intent_classifier/prompts.py
git commit -m "feat(intent_classifier): route deploy/fund-pick questions to additional_investment"
```

---

## Optional manual eval (not a gate)

`Agent_audit/questions.json` + `Agent_audit/run_audit.py` replay ~100 questions against a running backend and write `transcripts.json` for human inspection (no expected-intent field; fuzzy). After Plan 3 wires the flow, add ~6 `additional_investment` questions (category `"additional_investment"`) and replay to sanity-check routing + the full answer end-to-end. Not part of this plan's gates.

## Self-Review

**Spec coverage (spec §7 — classifier changes):**
- Add enum + `_IntentLiteral` + drift test → Task 1. ✓
- Add `### additional_investment` intent definition → Task 2 Step 3. ✓
- Move "which large-cap fund?" / "which fund is best?" out of rebalancing → Task 2 Step 6. ✓
- Move fresh-money deployment out of asset_allocation → Task 2 Step 5. ✓
- Keep swaps in rebalancing; keep SIP-feasibility in goal_planning → regression tests Task 2 Step 1; §7 distinctions Step 3. ✓
- Discriminator written crisply (deploy/select BUY-only vs move-existing vs target-mix vs feasibility) → §7 prose. ✓

**Deferred (correctly):** follow-up transition "AA accept → which funds → additional_investment" and the `flow.py`/handler wiring are Plan 3 (they need the app handler). Noted in Global Constraints.

**Placeholder scan:** none — every step has the exact text to add/remove and an exact command. (`<key>` in commands denotes the real API key the executor supplies.)

**Type consistency:** `Intent.ADDITIONAL_INVESTMENT` / value `"additional_investment"` used identically across models.py, `_IntentLiteral`, the docstring, and the tests.

## Downstream (Plan 3)

Plan 3 adds `flow_additional_investment` + the `FLOWS` row in `app/domains/ai_engine/services/flow.py`, the app domain + `@register("additional_investment")` handler, the input adapter, the formatter (surfacing `undeployed_inr`), and persistence + migration. Until Plan 3 lands, a question classified as `additional_investment` has no flow/handler.
