# fund_query Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a grounded `fund_query` chat capability that answers "why do we recommend fund X / what are its returns / how does it compare (to peers, or to another named fund)" from real data (ranking CSV + stored NAV), never fabricated.

**Architecture:** Two-step single-shot flow — an LLM **extract** pass pulls the fund name(s) + what's asked; deterministic Python **resolves** each fund to its canonical Direct-Growth `scheme_code` (or the customer's held scheme) and **builds** a facts container (`FundQueryFacts`: the named funds' facts + optional auto category-peers); an LLM **narrate** pass renders the answer under a strict "only pack numbers" guardrail. The bundled `AI_Agents/src/fund_query` engine is DB-agnostic; all ORM/DB reads live in the `mutual_funds` app-service.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, LangChain (`langchain-anthropic`), pytest (`asyncio_mode=auto`). Bundled agents loaded via `sys.path` injection.

> Revised after a 6-point plan audit (P1 model ordering, P2 unsound LLM test, P3 two-fund compare, P4 seams, P5 test infra, P6 resolver split). See the audit log at the end.

## Global Constraints

- **Repo is NOT git.** End each task at a "tests green" checkpoint; the human commits. Never run `git commit`/`git add`.
- **Run tests with** `.venv-mac/bin/python -m pytest` (from `Prozpr_Backend/`).
- **LLM calls go through LangChain** (`ChatAnthropic` / LCEL). Never `import anthropic` for `messages.create` (exception classes only).
- **Bundled-agent imports** use the injected path (`from fund_query import ...`), never `AI_Agents.*` qualified. App-service imports the agent under `ensure_ai_agents_path()`.
- **Latest models:** Haiku 4.5 (`claude-haiku-4-5-20251001`) for the extract/narrate passes, mirroring the classifier/portfolio_query.
- **Money is `float` rupees** in the mutual-funds/allocation families (not Decimal).
- **NAV reads are DB-only** — never call mfapi.in from this flow.
- **Prompt/behaviour lives in `.md`** rendered by `skill_executor` — not inlined.
- Register any new intent in BOTH the enum and `_IntentLiteral` (a drift test enforces parity).
- **DB tests:** create only the table under test (`await conn.run_sync(Model.__table__.create)`) — `Base.metadata.create_all` fails on SQLite (a Postgres `ARRAY` model). Reuse the repo's existing async-DB test fixture; do not invent one (see each DB task's Step 0).
- **LLM behaviour is not unit-tested.** Unit-test deterministic plumbing; the "no fabrication" guarantee lives in the eval gate + e2e (P2).

---

## File Structure

**New — bundled engine (`AI_Agents/src/fund_query/`):**
- `models.py` — `ExtractResult`, `FundReturns`, `FundFacts`, `PeerFund`, `FundQueryFacts`, `FundQueryResponse`.
- `orchestrator.py` — `FundQueryOrchestrator` with `extract(...)` and `narrate(...)`.
- `llm_client.py`, `skill_executor.py` — **copied verbatim** from `portfolio_query/`.
- `extract.md`, `fund_query.md`, `guardrails.md` — prompt sources.
- `__init__.py` — public exports.

**New — pure primitive:** `AI_Agents/src/financial_primitives/returns.py` — `cagr(...)`.

**New — app-layer (`app/domains/mutual_funds/services/`):**
- `fund_returns_service.py` — `trailing_cagr_for_scheme(db, scheme_code)`.
- `fund_ranking_lookup.py` — `ranking_by_isin`, `peers_by_sub_category`.
- `fund_resolver_service.py` — `resolve_fund` + `_match_fund_family` + `_pick_direct_growth`.
- `fund_query_service.py` — `build_fund_query_facts` + gateway `answer_fund_query`.

**Modified:** `app/domains/ai_engine/services/flow.py`; `AI_Agents/src/intent_classifier/{models.py,classifier.py,prompts.py}`; `app/domains/intent_classifier/services/intent_classifier_engine.py`.

**New — docs:** `AI_Agents/Reference_docs/Logics_reference_docs/fund_query.md`.

---

## Task 1: CAGR primitive

**Files:** Create `AI_Agents/src/financial_primitives/returns.py`; Test `AI_Agents/src/financial_primitives/Testing/test_returns.py` (follow the module's test-dir convention; create if absent).

**Interfaces — Produces:** `cagr(start_value: float, end_value: float, years: float) -> float | None` — annualized return as a percentage; `None` when `start_value <= 0` or `years <= 0`.

- [ ] **Step 1: Write the failing test**

```python
# AI_Agents/src/financial_primitives/Testing/test_returns.py
import pytest
from financial_primitives.returns import cagr

def test_cagr_doubles_over_three_years():
    assert cagr(100.0, 200.0, 3.0) == pytest.approx(25.99, abs=0.01)

def test_cagr_one_year_is_simple_return():
    assert cagr(100.0, 110.0, 1.0) == pytest.approx(10.0, abs=0.001)

def test_cagr_negative_when_value_fell():
    assert cagr(100.0, 90.0, 5.0) == pytest.approx(-2.085, abs=0.01)

def test_cagr_none_on_bad_inputs():
    assert cagr(0.0, 100.0, 3.0) is None
    assert cagr(100.0, 200.0, 0.0) is None
```

- [ ] **Step 2: Run — FAIL** (`.venv-mac/bin/python -m pytest AI_Agents/src/financial_primitives/Testing/test_returns.py -q`) — ModuleNotFoundError.
- [ ] **Step 3: Implement**

```python
# AI_Agents/src/financial_primitives/returns.py
"""Trailing-return primitives. Pure functions, no I/O."""
from __future__ import annotations
from typing import Optional

def cagr(start_value: float, end_value: float, years: float) -> Optional[float]:
    """Compound annual growth rate as a percentage; None on non-positive start/years."""
    if start_value <= 0 or years <= 0:
        return None
    return round(((end_value / start_value) ** (1.0 / years) - 1.0) * 100.0, 2)
```

- [ ] **Step 4: Run — PASS** (4 passed).
- [ ] **Step 5: Checkpoint.**

---

## Task 2: Scheme CAGR helper (DB NAV → 1/3/5y)

**Files:** Create `app/domains/mutual_funds/services/fund_returns_service.py`; Test `app/domains/mutual_funds/tests/test_fund_returns_service.py`.

**Interfaces:**
- Consumes: `cagr(...)` (T1); `MfNavHistory` (`app/domains/mutual_funds/models/mf_nav_history.py` — `scheme_code`, `nav`, `nav_date`).
- Produces: `async trailing_cagr_for_scheme(db, scheme_code: str) -> dict[str, float | None]` with keys `return_1y_cagr_pct`, `return_3y_cagr_pct`, `return_5y_cagr_pct`. DB-only; missing horizon → `None`.

- [ ] **Step 0 (P5): Reuse the repo's async-DB test pattern.** Grep existing async DB tests (e.g. `app/domains/*/tests/`, any `conftest.py`) for the shared async-session fixture; confirm the async SQLite driver is installed. Use that fixture below; only fall back to an inline engine if no shared fixture exists (and confirm the driver first). Keep the create-only-`MfNavHistory.__table__` rule.
- [ ] **Step 1: Write the failing test** — two behaviors: a 3y CAGR from a 100→200 NAV pair 3y apart ≈ 25.99; and a scheme with only 200 days of history → `return_1y_cagr_pct` and `return_3y_cagr_pct` are `None`. (Build the session via the Step-0 fixture; insert `MfNavHistory(scheme_code, nav, nav_date, scheme_name="X", mf_type="Open")` rows.)
- [ ] **Step 2: Run — FAIL** (ImportError).
- [ ] **Step 3: Implement**

```python
# app/domains/mutual_funds/services/fund_returns_service.py
"""Trailing CAGR for a scheme from stored NAV history. DB-only; no mfapi."""
from __future__ import annotations
import datetime as dt
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.domains.mutual_funds.models.mf_nav_history import MfNavHistory
from app.domains.ai_engine.common import ensure_ai_agents_path

ensure_ai_agents_path()
from financial_primitives.returns import cagr  # noqa: E402

_HORIZONS = (("return_1y_cagr_pct", 1), ("return_3y_cagr_pct", 3), ("return_5y_cagr_pct", 5))

async def trailing_cagr_for_scheme(db: AsyncSession, scheme_code: str) -> dict[str, Optional[float]]:
    rows = (await db.execute(
        select(MfNavHistory.nav, MfNavHistory.nav_date)
        .where(MfNavHistory.scheme_code == scheme_code)
        .order_by(MfNavHistory.nav_date)
    )).all()
    out: dict[str, Optional[float]] = {k: None for k, _ in _HORIZONS}
    if not rows:
        return out
    end_nav = float(rows[-1].nav)
    last_date = rows[-1].nav_date
    for key, years in _HORIZONS:
        cutoff = last_date - dt.timedelta(days=365 * years)
        start = next((float(r.nav) for r in reversed(rows) if r.nav_date <= cutoff), None)
        if start is not None:
            out[key] = cagr(start, end_nav, float(years))
    return out
```

- [ ] **Step 4: Run — PASS** (2 passed).
- [ ] **Step 5: Checkpoint.**

---

## Task 3: Fund-ranking lookup wrapper

**Files:** Create `app/domains/mutual_funds/services/fund_ranking_lookup.py`; Test `app/domains/mutual_funds/tests/test_fund_ranking_lookup.py`.

**Interfaces:**
- Consumes: `get_fund_ranking() -> dict[str, list[FundRankRow]]` and `FundRankRow` (`app/domains/rebalancing/services/rebal_engine/fund_rank.py`; fields `asset_subgroup, sub_category, rank, isin, fund_name, selection_reason, scheme_code`).
- Produces: `ranking_by_isin(isin) -> FundRankRow | None`; `peers_by_sub_category(sub_category, exclude_isin) -> list[FundRankRow]` (same sub_category, sorted by rank, self-excluded).

- [ ] **Step 1: Write the failing test** — pick any real row from `get_fund_ranking()`, assert `ranking_by_isin(row.isin).fund_name == row.fund_name` and `ranking_by_isin("NOT_AN_ISIN") is None`; find a `sub_category` with ≥2 ranked funds, assert `peers_by_sub_category(sub, exclude_isin=target.isin)` excludes the target and all share the `sub_category`.
- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement**

```python
# app/domains/mutual_funds/services/fund_ranking_lookup.py
"""Thin lookups over the cached fund-ranking CSV (by ISIN; peers by sub_category)."""
from __future__ import annotations
from app.domains.rebalancing.services.rebal_engine.fund_rank import FundRankRow, get_fund_ranking

def _all_rows() -> list[FundRankRow]:
    return [r for rows in get_fund_ranking().values() for r in rows]

def ranking_by_isin(isin: str) -> FundRankRow | None:
    return next((r for r in _all_rows() if r.isin == isin), None)

def peers_by_sub_category(sub_category: str, exclude_isin: str) -> list[FundRankRow]:
    peers = [r for r in _all_rows() if r.sub_category == sub_category and r.isin != exclude_isin]
    return sorted(peers, key=lambda r: r.rank)
```

- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Checkpoint.**

---

## Task 4: Fund resolver — two split helpers (P6)

**Files:** Create `app/domains/mutual_funds/services/fund_resolver_service.py`; Test `app/domains/mutual_funds/tests/test_fund_resolver_service.py`.

**Interfaces:**
- Consumes: `MfFundMetadata` (`app/domains/mutual_funds/models/mf_fund_metadata.py`).
- Produces:
  - `ResolvedFund(scheme_code: str, scheme_name: str, isin: str | None, is_held: bool)`
  - `Ambiguous(candidates: list[str])` (≤3 candidate scheme names)
  - `_match_fund_family(db, name: str) -> list[MfFundMetadata]` — fund-identity candidates.
  - `_pick_direct_growth(candidates: list[MfFundMetadata]) -> ResolvedFund | Ambiguous` — variant canonicalization.
  - `async resolve_fund(db, name: str, held: dict[str, str] | None = None) -> ResolvedFund | Ambiguous | None` — composes: held check → match family → pick Direct-Growth → confidence gate.

- [ ] **Step 0 (P5 + Direct-Growth seam): Inspect `MfFundMetadata` columns.** If it exposes `plan`/`option` fields, `_pick_direct_growth` filters on them (Direct + Growth); otherwise match the scheme name (`ILIKE '%direct%'` AND `ILIKE '%growth%'`). Record the choice in the docstring. Reuse the repo's async-DB test fixture (per Task 2 Step 0).
- [ ] **Step 1: Write the failing tests — one per concern:**
  - `_pick_direct_growth` picks the Direct-Growth scheme from a `[Direct-Growth, Regular-Growth]` pair.
  - `_match_fund_family` returns the right family among two lookalike funds ("Nippon Growth" vs "Nippon Large Cap").
  - `resolve_fund` with a `held` match → returns the held `scheme_code`, `is_held=True`.
  - `resolve_fund` with two distinct families matching → `Ambiguous`.
  - `resolve_fund` unknown name → `None`.
  (Seed via the Step-0 fixture, creating only `MfFundMetadata.__table__`.)
- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement** the two helpers + `resolve_fund` composition. Normalize names (lowercase, strip plan/option words) for matching; keep matching simple (normalized token overlap) — no fuzzy-match library.
- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Checkpoint.**

---

## Task 5 (was T6): fund_query engine models (P1 — defined before consumers)

**Files:** Create `AI_Agents/src/fund_query/__init__.py` (skeleton) + `AI_Agents/src/fund_query/models.py`; Test `AI_Agents/src/fund_query/Testing/test_models.py`.

**Interfaces — Produces (pydantic):**
- `FundReturns(return_1y_cagr_pct: float | None, return_3y_cagr_pct: float | None, return_5y_cagr_pct: float | None)`
- `FundFacts(fund_name: str, sub_category: str | None, returns: FundReturns | None, house_reason: str | None, shortlist_rank: int | None, has_house_view: bool)`
- `PeerFund(fund_name: str, return_3y_cagr_pct: float | None, shortlist_rank: int | None)`
- `FundQueryFacts(funds: list[FundFacts], peers: list[PeerFund])`
- `ExtractResult(fund_names: list[str], asked_for: Literal["reasoning","returns","comparison"])`
- `FundQueryResponse(answer: str, clarifying_question: str | None = None)`

- [ ] **Step 1: Write the failing test** — construct one of each model with valid data; assert defaults (`peers=[]`, `clarifying_question=None`) and that `asked_for` rejects an out-of-enum value.
- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement** `models.py` (BaseModel classes) + export them from `__init__.py`.
- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Checkpoint.**

---

## Task 6 (was T5): FundFacts builder → `FundQueryFacts` container (P3 two-fund)

**Files:** Create `app/domains/mutual_funds/services/fund_query_service.py` — `async build_fund_query_facts(db, resolved: list[ResolvedFund], asked_for: str) -> FundQueryFacts`; Test `app/domains/mutual_funds/tests/test_build_fund_query_facts.py`.

**Interfaces:**
- Consumes: `ResolvedFund` (T4), `trailing_cagr_for_scheme` (T2), `ranking_by_isin` + `peers_by_sub_category` (T3), the T5 models (`from fund_query import ...`).
- Produces: `FundQueryFacts`.
- **Rules:** for each resolved fund build a `FundFacts` (returns from T2; `house_reason`/`rank`/`sub_category`/`has_house_view` from `ranking_by_isin(isin)`; not in ranking → `has_house_view=False`, `house_reason=None`, returns still populated). Auto-peers **only** when `len(resolved) == 1` **and** `asked_for == "comparison"` **and** the fund has a `sub_category`: `peers_by_sub_category(...)` each with a 3y CAGR (DB-only). For `len(resolved) >= 2`, `peers=[]` (the named funds are the comparison set, each with full `FundFacts`).

- [ ] **Step 0 (P5):** reuse the async-DB fixture (Task 2 Step 0).
- [ ] **Step 1: Write the failing tests** (mock T2/T3): (a) single in-shortlist fund → `has_house_view=True`, reason + returns set; (b) single not-in-shortlist fund → `has_house_view=False`, `house_reason=None`, returns set; (c) single fund + `asked_for="comparison"` → `peers` populated, self-excluded; (d) two resolved funds → `len(funds)==2`, both full facts, `peers==[]`; (e) `asked_for="returns"` → `peers==[]`.
- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement** `build_fund_query_facts`.
- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Checkpoint.**

---

## Task 7: Bundled engine — two-pass orchestrator + prompts (P2 tests)

**Files:** Create `AI_Agents/src/fund_query/{orchestrator.py, extract.md, fund_query.md, guardrails.md}`; copy `llm_client.py` + `skill_executor.py` verbatim from `portfolio_query/`; expand `__init__.py`. Test `AI_Agents/src/fund_query/Testing/test_orchestrator.py`.

**Interfaces — Produces:** `FundQueryOrchestrator(api_key)` with `extract(question, history) -> ExtractResult` and `narrate(facts: FundQueryFacts, question, history) -> FundQueryResponse`.

**Reference (mirror, don't fork):** `portfolio_query/orchestrator.py` — `SkillExecutor.render(...)` → `persona.build_system_prompt(system_body, format_profile="chat", question_aware=True)` → `llm.call_structured(model, system, user, tool=..., max_tokens)`. Two forced-tool schemas: one → `ExtractResult`, one → `FundQueryResponse`.

**Prompt content (author in `.md`):**
- `extract.md` — name the fund(s) + classify `asked_for` (reasoning / returns / comparison).
- `fund_query.md` (narrate) — narrate `FundQueryFacts` as Pi; handle **both** modes: single fund + peers, and 2+ named funds head-to-head.
- `guardrails.md` — B1 rules: only state numbers present in the facts; never recompute/round/invent; frame rank as "our shortlist," never a category percentile; no house view → returns + "not one we actively recommend"; missing returns → say so.

- [ ] **Step 1: Write the failing tests — deterministic plumbing only (P2):** with the `LLMClient` stubbed to return a canned forced-tool payload, assert (a) `extract(...)` parses the payload into a valid `ExtractResult`; (b) `narrate(facts, …)` assembles a prompt that *includes* the facts + guardrail skill text and parses a canned payload into a `FundQueryResponse`. **Do NOT** assert on the LLM's word choice or "no fabricated number" — that lives in the eval gate + e2e.
- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement** orchestrator + prompts; copy the two helpers.
- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Checkpoint.**

---

## Task 8: App gateway + flow wiring (P3 multi-fund, P4 seams)

**Files:** Modify `app/domains/mutual_funds/services/fund_query_service.py` — add `async answer_fund_query(question, ctx) -> str`; Modify `app/domains/ai_engine/services/flow.py` — add `flow_fund_query` + FLOWS row. Test `app/domains/mutual_funds/tests/test_fund_query_service.py`.

**Sequence in `answer_fund_query`:**
1. `extract(question, history)`.
2. **(P4b)** empty `fund_names` → return a clarifying question ("Which fund would you like to know about?").
3. **(P4a)** build `held = {_normalize(h.instrument_name): h.fund_metadata.scheme_code for h in primary_portfolio.holdings if h.fund_metadata and h.fund_metadata.scheme_code}` from `ctx.user_ctx`.
4. **(P3)** resolve each name via `resolve_fund(db, name, held)`; any `Ambiguous` → return its clarifying question; drop `None`s (note unknown funds in the reply).
5. `build_fund_query_facts(db, resolved, asked_for)`.
6. `orchestrator.narrate(facts, question, history)` → return `answer or clarifying_question or _GENERIC_FAILURE_REPLY`.
Mirror `portfolio_query_service`'s API-key resolution + exception→canned-reply mapping. `flow_fund_query` mirrors `flow_portfolio_query` (`flow.py:75-81`), lazy-importing `answer_fund_query`; FLOWS row `"fund_query": flow_fund_query`.

- [ ] **Step 1: Write the failing tests** (orchestrator + resolver stubbed): (a) a normal single-fund question returns the narrated string; (b) empty `fund_names` → clarifying question, no narrate; (c) an ambiguous fund → its clarifying question, no narrate; (d) a two-fund question resolves both and passes 2 funds to the builder.
- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement** gateway + flow + FLOWS row.
- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Checkpoint.**

---

## Task 9: Intent registration + classifier prompt

**Files:** Modify `AI_Agents/src/intent_classifier/models.py` (`Intent.FUND_QUERY = "fund_query"`); `classifier.py` (`_IntentLiteral` += `"fund_query"` + docstring list); `prompts.py` (`### N. fund_query` section); `app/domains/intent_classifier/services/intent_classifier_engine.py` (`_INTENT_LABELS`). Test `app/domains/ai_engine/tests/test_intent_classifier_schema.py` (drift) + prompt eval-gate cases.

**Taxonomy entry (mirror the `portfolio_query` block):** definition ("customer wants to understand a *specific* fund — why we recommend it, its returns, how it compares"), triggers, examples ("why did you recommend Parag Parikh?", "its returns vs peers?", "compare Parag Parikh and HDFC Flexi Cap"), and **Key distinctions** vs `portfolio_query` (a fund they *hold* vs one recommended/asked about), `additional_investment` ("which fund should I buy" vs "tell me about this fund"), `general_market_query` (macro/indices vs a specific fund). Add follow-up guidance.

- [ ] **Step 1: Write/extend the failing test** — `Intent.FUND_QUERY` exists; drift test (enum == literal) passes; eval cases asserting the incident-style questions + a two-fund compare route to `fund_query`.
- [ ] **Step 2: Run — FAIL** (enum/literal mismatch or routing miss).
- [ ] **Step 3: Implement** the 4 edits + taxonomy entry.
- [ ] **Step 4: Run** the drift test + `scripts/run_prompt_eval_gate.sh` — drift green; eval-gate not regressed; fund_query cases pass. **This is where the anti-fabrication behaviour is asserted (P2)** — add an eval case checking an answer's numbers match known DB/CSV values and no category-percentile phrasing.
- [ ] **Step 5: Checkpoint.**

---

## Task 10: Logic reference doc

**Files:** Create `AI_Agents/Reference_docs/Logics_reference_docs/fund_query.md`.

- [ ] **Step 1:** Document: what fund_query answers; Direct-Growth canonicalization + confidence-gate/clarify; DB-NAV CAGR returns (no mfapi); like-for-like `sub_category` peers + explicit two-fund compares; house-shortlist-rank framing (never a category percentile); the grounding rule. Add the version-bump header matching sibling docs.
- [ ] **Step 2: Checkpoint.**

---

## Task 11: End-to-end verification (P2 anti-fabrication lives here + eval gate)

- [ ] **Step 1:** Drive the flow against a dev DB with Sourabh's three questions **plus a two-fund compare** ("compare Parag Parikh and HDFC Flexi Cap"). Confirm: routes to `fund_query`; returns come from stored NAV (CAGR); reasoning from the CSV; peers are flexi-cap; the two-fund compare shows both funds' full facts; **no fabricated "3 of 24" or "per our live data"**; ambiguous/unknown funds ask or decline; a held fund uses the held scheme.
- [ ] **Step 2:** Full affected suites green: `.venv-mac/bin/python -m pytest AI_Agents/src/financial_primitives AI_Agents/src/fund_query app/domains/mutual_funds app/domains/ai_engine -q`.
- [ ] **Step 3: Checkpoint.**

---

## Self-review

- **Spec coverage:** CAGR primitive (T1), scheme CAGR (T2), CSV lookup + peers (T3), resolver split (T4), engine models (T5), FundFacts builder + two-fund container (T6), orchestrator/guardrail (T7), gateway/flow + P4 seams (T8), intent + eval anti-fabrication (T9), Logic doc (T10), e2e incl. two-fund compare (T11). Non-goals honored by construction.
- **Ordering (P1):** models (T5) precede both consumers — builder (T6) and orchestrator (T7). ✓
- **Placeholder scan:** the only "to confirm" is the Direct-Growth column seam — an explicit Step-0 action in T4. ✓
- **Type consistency:** `FundReturns`/`FundFacts`/`PeerFund`/`FundQueryFacts`/`ExtractResult`/`FundQueryResponse` defined in T5, consumed with matching names in T6/T7/T8; `trailing_cagr_for_scheme` keys (`return_{1,3,5}y_cagr_pct`) consistent T2↔T6; `resolve_fund` union (`ResolvedFund`/`Ambiguous`/`None`) consistent T4↔T8. ✓

## Audit log — plan (2026-07-07)

| # | Finding | Decision |
|---|---|---|
| P1 | Models consumed before defined | Dedicated models task (T5) before builder/orchestrator; renumbered |
| P2 | LLM non-fabrication unit test unsound | Unit-test deterministic plumbing only; anti-fabrication → eval gate (T9) + e2e (T11) |
| P3 | Two named funds not handled | Build it via `FundQueryFacts` container (funds[] + peers[]) |
| P4 | Under-specified seams | `held` map from `ctx.user_ctx`; empty `fund_names` → clarify (T8) |
| P5 | Bespoke async-DB test harness | Step-0 reuse the repo's async-DB fixture + confirm driver (T2/T4/T6) |
| P6 | Resolver monolith / risky seam | Split into `_match_fund_family` + `_pick_direct_growth`, each tested (T4) |
