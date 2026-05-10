# app/services/ai_bridge/ — AI agent adapters

Adapters between `ChatBrain` and the `AI_Agents` orchestrators. One file per
intent branch; each maps ORM User + inputs to an agent's DTO, calls the agent,
and formats the reply for chat.

## Files

- `common.py` — `ensure_ai_agents_path()`, `build_history_block()` shared helpers.
- `intent_classifier_service.py` — `classify_user_message`; wraps AI_Agents intent classifier.
- `market_commentary_service.py` — `generate_market_commentary`; wraps market commentary agent.
- `general_chat_service.py` — `generate_general_chat_response`; general-chat reply generator.
- `portfolio_query_service.py` — `generate_portfolio_query_response`; wraps portfolio query agent.
- `chart_selector_service.py` — caller for the `chart_selector` agent; runs in parallel with text generation against the live catalogue from `app/services/visualization_tools/registry.CHART_TOOLS`.
- `chat_dispatcher.py` — per-intent chat handler registry (`@register`, `dispatch_chat`).
- `ailax_flow.py` — `detect_spine_mode`, `build_ailax_spine`; portfolio spine orchestration.
- `__init__.py` — re-exports bridge entry points consumed by `chat_core`.

## Child packages

- **asset_allocation/** — allocation domain: engine adapter (`service.py`),
  unified chat handler (`chat.py`), input builder (`input_builder.py`),
  and `tests/` co-located with the package. Note: `chat.py` is *not*
  auto-imported by `asset_allocation/__init__.py` (would cycle through
  `chat_core.turn_context`); callers needing its `@register` side-effect
  must import it lazily.
- **rebalancing/** — rebalancing domain: engine adapter (`service.py`), chat
  handler (`chat.py`), input builder (`input_builder.py`), tax-aging
  (`tax_aging.py`), holdings ledger (`holdings_ledger.py`), fund ranking
  (`fund_rank.py`), chart picker (`chart_picker.py`, `charts.py`), and
  `formatter.py`; bridges `AI_Agents/src/Rebalancing` into chat.
- **answer_formatter/** — shared question-aware answer formatter
  (`formatter.py` exposes `format_with_telemetry`, `FactsPack`, `ActionMode`,
  `FormatterFailure`); per-module chat bridges call this instead of
  hand-templating strings.

## Entry point

- Each bridge module exposes a top-level function; all imported by
  `app/services/chat_core/brain.py`.
- `ensure_ai_agents_path()` is called once per process to inject `AI_Agents/src`
  into `sys.path`.

## Depends on

- `AI_Agents/src/*` — each bridge calls into one agent (intent, market commentary,
  portfolio query, allocation, rebalancing, chart_selector).
- `app/services/visualization_tools/registry` — `chart_selector_service` reads `CHART_TOOLS`.
- `app/models/*` — reads ORM User / portfolio / goals data.

## Tests

- Command: `pytest app/services/ai_bridge/ -v` (covers the shared
  `tests/` folder plus `asset_allocation/tests/` and `rebalancing/tests/`).
- Domain-specific suites live alongside their packages
  (`asset_allocation/tests/`, `rebalancing/tests/`,
  `answer_formatter/tests/`).
- Shared suites in `tests/`: `test_chat_dispatcher.py`,
  `test_classifier_service_active_intent.py`.

## Flow

**Allocation spine** (`build_ailax_spine` → `compute_allocation_result`)
1. Ensure fund view file is present (copy from Reference_files fallback if needed).
2. Map ORM User to allocation DTOs via `asset_allocation.input_builder.build_goal_allocation_input_for_user`.
3. Invoke `AllocationOrchestrator` with the mapped DTOs.
4. `asset_allocation.service.format_allocation_chat_brief` to produce chat-ready reply.

## Don't read

- `__pycache__/`.
- `tests/` — test fixtures, not source of truth.
