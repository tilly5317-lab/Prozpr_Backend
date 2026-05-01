# ai_bridge — Data Flow & Module Reference

> This package sits between the FastAPI routers and the `AI_Agents/src`
> packages.  It handles API key resolution, async ↔ thread bridging,
> user-context mapping, and markdown formatting so that the AI engine
> modules (`asset_allocation_pydantic`, `intent_classifier`,
> `market_commentary`, `risk_profiling`) stay untouched.

---

## High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                              FRONTEND (React)                                    │
│  AIChatPanel.tsx  ──POST /chat/sessions/{id}/messages──▶  api.ts                │
└───────────────────────────────────────────┬──────────────────────────────────────┘
                                            │
                                            ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                           FASTAPI ROUTER LAYER                                   │
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐    │
│  │  chat.py  (app/routers/chat.py)                                         │    │
│  │                                                                          │    │
│  │  send_message()                                                          │    │
│  │    IN:  session_id, user message text, client_context                    │    │
│  │    OUT: { user_message, assistant_message, rebalancing_id, snapshot_id } │    │
│  │                                                                          │    │
│  │  1. Load conversation history from DB (chat_context.py)                  │    │
│  │  2. Persist user message                                                 │    │
│  │  3. Call ChatBrain().run_turn(...)                                        │    │
│  │  4. Persist assistant reply                                              │    │
│  │  5. Return both messages + metadata                                      │    │
│  └──────────────────────────────────┬───────────────────────────────────────┘    │
└─────────────────────────────────────┼───────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                        CHAT CORE  (app/services/chat_core/)                      │
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐    │
│  │  brain.py  — ChatBrain.run_turn()                                       │    │
│  │                                                                          │    │
│  │  IN:  ChatTurnInput { user_ctx, user_question, conversation_history,    │    │
│  │                        client_context, session_id, db, user_id }        │    │
│  │  OUT: ChatBrainResult { content, intent, confidence, reasoning,         │    │
│  │                          rebalancing_id, snapshot_id }                   │    │
│  │                                                                          │    │
│  │  Dispatches to one of 4 branches based on classified intent:            │    │
│  │    ┌─────────────────┬──────────────────────────────────────────────┐   │    │
│  │    │  Intent          │  Module(s) Called                            │   │    │
│  │    ├─────────────────┼──────────────────────────────────────────────┤   │    │
│  │    │  general_market  │  market_commentary_service                  │   │    │
│  │    │  _query          │  → general_chat_service                     │   │    │
│  │    ├─────────────────┼──────────────────────────────────────────────┤   │    │
│  │    │  portfolio       │  ailax_flow (spine mode + allocation)       │   │    │
│  │    │  _optimisation   │  → asset_allocation_service                 │   │    │
│  │    │  / goal_planning │  → (optionally liquidity_gate for cash-out) │   │    │
│  │    ├─────────────────┼──────────────────────────────────────────────┤   │    │
│  │    │  portfolio_query │  portfolio_query_service (DB only, no LLM)  │   │    │
│  │    ├─────────────────┼──────────────────────────────────────────────┤   │    │
│  │    │  out_of_scope /  │  general_chat_service                       │   │    │
│  │    │  other           │                                              │   │    │
│  │    └─────────────────┴──────────────────────────────────────────────┘   │    │
│  └──────────────────────────────────┬───────────────────────────────────────┘    │
└─────────────────────────────────────┼───────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                    AI BRIDGE  (app/services/ai_bridge/)                          │
│                                                                                  │
│  The numbered boxes below are the 11 files in this package.                      │
│  Arrows show call direction; labels show key I/O data.                           │
│                                                                                  │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │  [1]  __init__.py                                                          │  │
│  │  Re-exports the 5 public functions consumed by brain.py:                   │  │
│  │    • classify_user_message          (from intent_classifier_service)       │  │
│  │    • format_intent_response         (from intent_classifier_service)       │  │
│  │    • generate_general_chat_response (from general_chat_service)            │  │
│  │    • generate_market_commentary     (from market_commentary_service)       │  │
│  │    • generate_portfolio_query_response (from portfolio_query_service)      │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │  [2]  common.py                                                            │  │
│  │  Shared utilities used by every other module.                             │  │
│  │                                                                           │  │
│  │  Functions:                                                               │  │
│  │    ensure_ai_agents_path()                                                │  │
│  │      Adds AI_Agents/src to sys.path so agent packages are importable.     │  │
│  │      Called at module load time by every service that imports from         │  │
│  │      AI_Agents (intent_classifier, asset_allocation_pydantic, etc.). │  │
│  │                                                                           │  │
│  │    build_history_block(history: list[dict]) → str                         │  │
│  │      IN:  last N chat turns [{role, content}, ...]                        │  │
│  │      OUT: formatted text block for LLM prompts                            │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │  [3]  ailax_trace.py                                                      │  │
│  │  Lightweight stdout tracing prefixed with [AILAX_TRACE].                  │  │
│  │                                                                           │  │
│  │  Functions:                                                               │  │
│  │    trace_line(message)              → prints "[AILAX_TRACE] ..."          │  │
│  │    trace_response_preview(label, text, max_chars)  → truncated preview    │  │
│  │                                                                           │  │
│  │  Used by: brain.py, asset_allocation_service.py                           │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
│                                                                                  │
│  ═══════════════════════════════════════════════════════════════════════════════  │
│                         INTENT CLASSIFICATION                                    │
│  ═══════════════════════════════════════════════════════════════════════════════  │
│                                                                                  │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │  [4]  intent_classifier_service.py                                        │  │
│  │                                                                           │  │
│  │  classify_user_message(question, history)                                 │  │
│  │    IN:  customer question (str) + conversation history                    │  │
│  │    OUT: ClassificationResult { intent, confidence, reasoning }            │  │
│  │                                                                           │  │
│  │    Intent enum values:                                                    │  │
│  │      asset_allocation | goal_planning | portfolio_query             │  │
│  │      general_market_query   | out_of_scope                               │  │
│  │                                                                           │  │
│  │    API calls:                                                             │  │
│  │      PRIMARY   → Anthropic (Claude) via AI_Agents IntentClassifier        │  │
│  │      FALLBACK  → OpenAI  POST /v1/chat/completions  (gpt-4o-mini)        │  │
│  │                  function calling: classify_intent                         │  │
│  │                                                                           │  │
│  │  format_intent_response(result) → formatted string (for debugging)        │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
│                          │                                                       │
│             ClassificationResult                                                 │
│                          │                                                       │
│            ┌─────────────┼─────────────────────────┐                             │
│            ▼             ▼                          ▼                             │
│                                                                                  │
│                                                                                  │
│  ═══════════════════════════════════════════════════════════════════════════════  │
│                     BRANCH A: GENERAL / MARKET                                   │
│  ═══════════════════════════════════════════════════════════════════════════════  │
│                                                                                  │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │  [5]  market_commentary_service.py                                        │  │
│  │                                                                           │  │
│  │  generate_market_commentary(question, history)                            │  │
│  │    IN:  user question (reserved for future use), conversation history     │  │
│  │    OUT: markdown string — macro-economic market commentary document       │  │
│  │                                                                           │  │
│  │    Pipeline (3 attempts in priority order):                               │  │
│  │                                                                           │  │
│  │    1. FAST PATH: Read cached MacroSnapshot from disk                      │  │
│  │       (market_commentary_cache.json, max age 1h)                          │  │
│  │       → DocumentGenerator (Anthropic Sonnet)                              │  │
│  │                                                                           │  │
│  │    2. FULL ANTHROPIC:                                                     │  │
│  │       MarketCommentaryAgent.run()                                         │  │
│  │         → IndicatorScraper.scrape_all() (DuckDuckGo, 14 indicators)      │  │
│  │         → Anthropic Haiku (extract structured MacroSnapshot)              │  │
│  │         → Anthropic Sonnet (generate commentary document)                 │  │
│  │                                                                           │  │
│  │    3. OPENAI FALLBACK:                                                    │  │
│  │       IndicatorScraper.scrape_all()                                       │  │
│  │         → OpenAI POST /v1/chat/completions (gpt-4o-mini, extract)         │  │
│  │         → OpenAI POST /v1/chat/completions (gpt-4o-mini, generate)        │  │
│  └──────────────────────────────────┬─────────────────────────────────────────┘  │
│                                     │ commentary string (optional)               │
│                                     ▼                                            │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │  [6]  general_chat_service.py                                             │  │
│  │                                                                           │  │
│  │  generate_general_chat_response(question, classification,                 │  │
│  │                                  market_commentary, history, context)      │  │
│  │    IN:  user question, ClassificationResult, optional market doc,         │  │
│  │         conversation history, client context from DB                       │  │
│  │    OUT: markdown string — Answer + Justification                          │  │
│  │                                                                           │  │
│  │    API call:                                                              │  │
│  │      → OpenAI POST /v1/chat/completions (gpt-4o-mini, max 420 tokens)    │  │
│  │                                                                           │  │
│  │    Special case: out_of_scope intent returns canned OUT_OF_SCOPE_MESSAGE  │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
│                                                                                  │
│  ═══════════════════════════════════════════════════════════════════════════════  │
│                   BRANCH B: PORTFOLIO QUERY (DB ONLY)                            │
│  ═══════════════════════════════════════════════════════════════════════════════  │
│                                                                                  │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │  [7]  portfolio_query_service.py                                          │  │
│  │                                                                           │  │
│  │  generate_portfolio_query_response(user, question)                        │  │
│  │    IN:  User ORM object (with portfolios + goals loaded), question text   │  │
│  │    OUT: markdown string — portfolio value, gain/loss, top allocations,    │  │
│  │         goal names                                                        │  │
│  │                                                                           │  │
│  │    API calls: NONE (pure DB read — no external LLM)                       │  │
│  │    Data source: user.portfolios[].total_value, allocations, financial_    │  │
│  │                 goals                                                     │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
│                                                                                  │
│  ═══════════════════════════════════════════════════════════════════════════════  │
│            BRANCH C: PORTFOLIO OPTIMISATION / GOAL PLANNING                      │
│              (the main allocation pipeline — 5 modules deep)                     │
│  ═══════════════════════════════════════════════════════════════════════════════  │
│                                                                                  │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │  [8]  ailax_flow.py                                                       │  │
│  │                                                                           │  │
│  │  detect_spine_mode(question) → SpineMode enum                             │  │
│  │    IN:  user question text                                                │  │
│  │    OUT: one of: FULL | CASH_IN | CASH_OUT | DRIFT_CHECK | REBALANCE      │  │
│  │    Uses regex patterns to classify the portfolio sub-intent.              │  │
│  │                                                                           │  │
│  │  build_ailax_spine(user, question, mode, db, ...)                         │  │
│  │    IN:  User ORM, question, SpineMode, DB session, flags                  │  │
│  │    OUT: AilaxSpineResult { text, rebalancing_id, snapshot_id }            │  │
│  │                                                                           │  │
│  │    Calls:  asset_allocation_service.compute_allocation_result()            │  │
│  │    Then:   format_allocation_chat_brief() to build the chat markdown      │  │
│  │    Prefix: user's risk profile category (if available)                    │  │
│  └──────────────────────────────────┬─────────────────────────────────────────┘  │
│                                     │                                            │
│          if CASH_OUT mode           │  all other modes                           │
│          ┌──────────────────┐       │                                            │
│          ▼                  │       │                                            │
│  ┌──────────────────────┐   │       │                                            │
│  │  [9] liquidity_gate  │   │       │                                            │
│  │       .py            │   │       │                                            │
│  │                      │   │       │                                            │
│  │  assess_liquidity_   │   │       │                                            │
│  │    for_cash_out()    │   │       │                                            │
│  │  IN:  User, question │   │       │                                            │
│  │  OUT: Gate result    │   │       │                                            │
│  │   {sufficient,       │   │       │                                            │
│  │    inferred_need,    │   │       │                                            │
│  │    emergency_fund}   │   │       │                                            │
│  │                      │   │       │                                            │
│  │  If sufficient:      │   │       │                                            │
│  │   → short checklist  │   │       │                                            │
│  │   (skip allocation)  │   │       │                                            │
│  │  Else:               │   │       │                                            │
│  │   → continue to ──────┘  │       │                                            │
│  │     full pipeline        │       │                                            │
│  │                          │       │                                            │
│  │  API: NONE (heuristic)   │       │                                            │
│  └──────────────────────────┘       │                                            │
│                                     ▼                                            │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │  [10]  asset_allocation_service.py                                        │  │
│  │                                                                           │  │
│  │  compute_allocation_result(user, question, db, ...)                       │  │
│  │    IN:  User ORM, question, db session, persist flags, spine_mode         │  │
│  │    OUT: AllocationRunOutcome { result: GoalAllocationOutput | None,       │  │
│  │           blocking_message, rebalancing_id, snapshot_id }                 │  │
│  │                                                                           │  │
│  │    Orchestration steps:                                                   │  │
│  │    1. Guard: check date_of_birth exists                                   │  │
│  │    2. Call goal_allocation_input_builder                                  │  │
│  │         .build_goal_allocation_input_for_user()                           │  │
│  │    3. Resolve Anthropic API key from settings                             │  │
│  │    4. Call asset_allocation_pydantic.pipeline                       │  │
│  │         .run_allocation_with_state() in a thread (asyncio.to_thread),     │  │
│  │         passing generate_rationales as the optional LLM rationale step    │  │
│  │    5. Trace all 7 pipeline steps to [AILAX_TRACE]                         │  │
│  │    6. Optionally persist recommendation to DB                             │  │
│  │                                                                           │  │
│  │  format_allocation_chat_brief(GoalAllocationOutput, spine_mode) → markdown│  │
│  │    Renders: risk score, target mix (%), carve-outs, fund-level rows,      │  │
│  │    unallocated gap, grand total reconciliation — all to 0.01 precision    │  │
│  │                                                                           │  │
│  │  generate_asset_allocation_response(user, question, db)             │  │
│  │    Wrapper for standalone HTTP use (not via chat flow).                   │  │
│  │                                                                           │  │
│  │  Pipeline steps (run inside asset_allocation_pydantic):              │  │
│  │    Step 1: Emergency fund carve-out                                       │  │
│  │    Step 2: Short-term goal allocation                                     │  │
│  │    Step 3: Medium-term goal allocation                                    │  │
│  │    Step 4: Long-term goal allocation                                      │  │
│  │    Step 5: Aggregation across goals                                       │  │
│  │    Step 6: Guardrails / bounds enforcement                                │  │
│  │    Step 7: Presentation (optional Anthropic Claude rationale)             │  │
│  └──────────────────────────────────┬─────────────────────────────────────────┘  │
│                                     │ calls [11]                                 │
│                                     ▼                                            │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │  [11]  goal_allocation_input_builder.py                                   │  │
│  │                                                                           │  │
│  │  build_goal_allocation_input_for_user(user)                               │  │
│  │    IN:  User ORM (with risk_profile, investment_profile, financial_goals, │  │
│  │         portfolios, effective_risk_assessments loaded)                    │  │
│  │    OUT: (AllocationInput, debug_dict)                                     │  │
│  │                                                                           │  │
│  │  Reads from user:                                                         │  │
│  │    • date_of_birth → age                                                  │  │
│  │    • investment_profile (income, liabilities, horizon)                    │  │
│  │    • financial_goals → Goal list                                          │  │
│  │    • portfolios → total_corpus                                            │  │
│  │    • effective_risk_assessments → effective_risk_score (fallback 7.0)     │  │
│  │                                                                           │  │
│  │  API calls: NONE (pure DB + computation, no call into scoring module)     │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

---

## Module-by-Module Reference

### `__init__.py`


| Function                            | Imported From               | Purpose                       |
| ----------------------------------- | --------------------------- | ----------------------------- |
| `classify_user_message`             | `intent_classifier_service` | Classify user intent          |
| `format_intent_response`            | `intent_classifier_service` | Debug-format a classification |
| `generate_general_chat_response`    | `general_chat_service`      | General / market chat reply   |
| `generate_market_commentary`        | `market_commentary_service` | Macro-economic document       |
| `generate_portfolio_query_response` | `portfolio_query_service`   | DB-only portfolio summary     |


### `common.py`


| Function                       | I/O                          | Notes                                   |
| ------------------------------ | ---------------------------- | --------------------------------------- |
| `ensure_ai_agents_path()`      | `→ None`                     | Adds `AI_Agents/src` to `sys.path` once |
| `build_history_block(history)` | `list[{role,content}] → str` | Last 6 turns formatted for LLM          |


### `ailax_trace.py`


| Function                             | I/O                | Notes                    |
| ------------------------------------ | ------------------ | ------------------------ |
| `trace_line(msg)`                    | `str → stdout`     | Prefixed `[AILAX_TRACE]` |
| `trace_response_preview(label,text)` | `str,str → stdout` | Truncated preview        |


### `intent_classifier_service.py`


| Function                            | I/O                                | External API                                               |
| ----------------------------------- | ---------------------------------- | ---------------------------------------------------------- |
| `classify_user_message(q, history)` | `str, list → ClassificationResult` | **Anthropic** (primary), **OpenAI gpt-4o-mini** (fallback) |
| `format_intent_response(result)`    | `ClassificationResult → str`       | None                                                       |
| `intent_labels()`                   | `→ dict[str,str]`                  | None                                                       |


### `market_commentary_service.py`


| Function                                 | I/O               | External API                                                                                     |
| ---------------------------------------- | ----------------- | ------------------------------------------------------------------------------------------------ |
| `generate_market_commentary(q, history)` | `str, list → str` | **DuckDuckGo** (scrape), **Anthropic Haiku+Sonnet** (primary), **OpenAI gpt-4o-mini** (fallback) |


### `general_chat_service.py`


| Function                                                   | I/O                                                   | External API           |
| ---------------------------------------------------------- | ----------------------------------------------------- | ---------------------- |
| `generate_general_chat_response(q, class, mkt, hist, ctx)` | `str, ClassificationResult, str?, list?, dict? → str` | **OpenAI gpt-4o-mini** |


### `portfolio_query_service.py`


| Function                                     | I/O               | External API                      |
| -------------------------------------------- | ----------------- | --------------------------------- |
| `generate_portfolio_query_response(user, q)` | `User, str → str` | **None** (reads ORM objects only) |


### `ailax_flow.py`


| Function                                    | I/O                                       | External API                            |
| ------------------------------------------- | ----------------------------------------- | --------------------------------------- |
| `detect_spine_mode(q)`                      | `str → SpineMode`                         | None (regex)                            |
| `build_ailax_spine(user, q, mode, db, ...)` | `User, str, SpineMode → AilaxSpineResult` | Delegates to `asset_allocation_service` |


### `liquidity_gate.py`


| Function                                        | I/O                                    | External API     |
| ----------------------------------------------- | -------------------------------------- | ---------------- |
| `assess_liquidity_for_cash_out(user, q)`        | `User, str → LiquidityGateResult`      | None (heuristic) |
| `format_quick_cash_out_response(user, q, gate)` | `User, str, LiquidityGateResult → str` | None             |


### `asset_allocation_service.py`


| Function                                                | I/O                                | External API                                         |
| ------------------------------------------------------- | ---------------------------------- | ---------------------------------------------------- |
| `compute_allocation_result(user, q, db, ...)`           | `User, str → AllocationRunOutcome` | **Anthropic (Claude)** via pydantic pipeline rationale step |
| `format_allocation_chat_brief(output, mode)`            | `GoalAllocationOutput, str → str`  | None (pure formatting)                               |
| `generate_asset_allocation_response(user, q, db)` | `User, str → str`                  | Same as `compute_allocation_result`                  |


### `goal_allocation_input_builder.py`


| Function                                  | I/O                                    | External API                             |
| ----------------------------------------- | -------------------------------------- | ---------------------------------------- |
| `build_goal_allocation_input_for_user(u)` | `User → (AllocationInput, debug_dict)` | None (pure DB + computation)             |


---

## External API Summary


| Provider       | Model                         | Used By                     | Purpose                                     |
| -------------- | ----------------------------- | --------------------------- | ------------------------------------------- |
| **Anthropic**  | Claude (via LangChain)        | `asset_allocation_service`  | Optional LLM rationale in the 7-step pydantic allocation pipeline |
| **Anthropic**  | Claude (via IntentClassifier) | `intent_classifier_service` | Intent classification (primary)             |
| **Anthropic**  | Haiku + Sonnet                | `market_commentary_service` | Macro data extraction + document generation |
| **OpenAI**     | gpt-4o-mini                   | `intent_classifier_service` | Intent classification (fallback)            |
| **OpenAI**     | gpt-4o-mini                   | `general_chat_service`      | General/market chat response                |
| **OpenAI**     | gpt-4o-mini                   | `market_commentary_service` | Market commentary (fallback)                |
| **DuckDuckGo** | Web search                    | `market_commentary_service` | Scrape 14 Indian market indicators          |


---

## Environment Variables Required


| Variable                              | Used By                                                                        | Fallback                                        |
| ------------------------------------- | ------------------------------------------------------------------------------ | ----------------------------------------------- |
| `ANTHROPIC_API_KEY`                   | `intent_classifier`, `asset_allocation_service`, `market_commentary`           | —                                               |
| `ASSET_ALLOCATION_API_KEY`            | `asset_allocation_service` (overrides `ANTHROPIC_API_KEY` for allocation)      | `PORTFOLIO_QUERY_API_KEY` → `ANTHROPIC_API_KEY` |
| `OPENAI_API_KEY`                      | `general_chat`, `intent_classifier` (fallback), `market_commentary` (fallback) | —                                               |
| `MARKET_COMMENTARY_CACHE_MAX_AGE_SEC` | `market_commentary_service`                                                    | `3600` (1 hour)                                 |


---

## Data Flow: Complete Request Lifecycle

```
User types message
        │
        ▼
  ┌─ chat.py ──────────────────────────────────────────────────────────────┐
  │  load_conversation_history() from DB                                   │
  │  persist user message                                                  │
  │  call ChatBrain().run_turn(ChatTurnInput)                              │
  └───────────────────────────┬────────────────────────────────────────────┘
                              │
                              ▼
  ┌─ brain.py ─────────────────────────────────────────────────────────────┐
  │  Step 1: classify_user_message(question, history)                      │
  │            → Anthropic Claude  (or OpenAI fallback)                    │
  │            → returns intent + confidence + reasoning                   │
  │                                                                        │
  │  Step 2: dispatch by intent ───────────────────────────────────────┐   │
  │                                                                    │   │
  │    ┌─ general_market_query ──────────────────────────────────────┐ │   │
  │    │  market_commentary_service.generate_market_commentary()     │ │   │
  │    │    → DuckDuckGo scrape + Anthropic/OpenAI extraction        │ │   │
  │    │  general_chat_service.generate_general_chat_response()      │ │   │
  │    │    → OpenAI gpt-4o-mini                                     │ │   │
  │    │  returns: markdown Answer + Justification                   │ │   │
  │    └─────────────────────────────────────────────────────────────┘ │   │
  │                                                                    │   │
  │    ┌─ asset_allocation / goal_planning ────────────────────┐ │   │
  │    │  ailax_flow.detect_spine_mode(question) → SpineMode         │ │   │
  │    │                                                             │ │   │
  │    │  if CASH_OUT:                                               │ │   │
  │    │    liquidity_gate.assess_liquidity() → sufficient?          │ │   │
  │    │    if yes → quick cash-out checklist (no LLM needed)        │ │   │
  │    │                                                             │ │   │
  │    │  ailax_flow.build_ailax_spine()                              │ │   │
  │    │    → asset_allocation_service.compute_allocation_result()    │ │   │
  │    │      → goal_allocation_input_builder                         │ │   │
  │    │          .build_goal_allocation_input_for_user()             │ │   │
  │    │        → reads risk_profile + investment_profile + goals     │ │   │
  │    │        → returns AllocationInput + debug                     │ │   │
  │    │      → asset_allocation_pydantic.pipeline               │ │   │
  │    │          .run_allocation_with_state()                        │ │   │
  │    │        → 7 pure-Python steps + optional Anthropic Claude     │ │   │
  │    │          rationale via generate_rationales                   │ │   │
  │    │        → returns GoalAllocationOutput                        │ │   │
  │    │      → optionally persist to DB                             │ │   │
  │    │    → format_allocation_chat_brief() → markdown              │ │   │
  │    │  returns: risk header + allocation markdown + IDs           │ │   │
  │    └─────────────────────────────────────────────────────────────┘ │   │
  │                                                                    │   │
  │    ┌─ portfolio_query ───────────────────────────────────────────┐ │   │
  │    │  portfolio_query_service.generate_portfolio_query_response() │ │   │
  │    │    → reads user.portfolios + user.financial_goals from ORM  │ │   │
  │    │  returns: portfolio summary markdown                        │ │   │
  │    └─────────────────────────────────────────────────────────────┘ │   │
  │                                                                    │   │
  │    ┌─ out_of_scope / other ──────────────────────────────────────┐ │   │
  │    │  general_chat_service.generate_general_chat_response()      │ │   │
  │    │    → OpenAI gpt-4o-mini (or canned out-of-scope message)   │ │   │
  │    └─────────────────────────────────────────────────────────────┘ │   │
  │                                                                        │
  │  Step 3: finalize → log telemetry, return ChatBrainResult             │
  └───────────────────────────┬────────────────────────────────────────────┘
                              │
                              ▼
  ┌─ chat.py ──────────────────────────────────────────────────────────────┐
  │  persist assistant message                                             │
  │  return { user_message, assistant_message, rebalancing_id, snapshot }  │
  └────────────────────────────────────────────────────────────────────────┘
```

