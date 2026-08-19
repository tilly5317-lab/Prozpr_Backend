# app/domains/financial_planning/ — the customer's plan, and the facts behind it

One domain for what used to be two intents (`goal_planning` + `profile_update`) and two packages (`profile_capture/` + `goal_capture/`). It owns everything a customer can say about their own financial position: stating or relatively adjusting a figure, creating / editing / removing a goal, reading any of it back, and asking what the plan does with it.

## Entry / contract
- Public API: `services.planning_module_service.run(turn, ctx, prior)` → `ModuleOutput`. Called only by `ai_engine`'s `flow_financial_planning`.
- `ai_engine.planning_gate` decides the turn belongs here and attaches a `PlanningDirective` to `TurnContext.planning_directive`.
- `routers/planning_router.py` mounts two endpoints under `/chat`: `POST .../planning/undo` and `GET .../planning/state`. There is deliberately no answer/skip endpoint — Pi asks in prose and the customer answers in prose.

## The turn
1. `planning_extractor.read_message` — builds the agent's input from the registry, calls `AI_Agents/src/response_extractor/`, and resolves the reply → a list of typed `Operation`s (target × verb), plus `kind`, `unchanged_fields` and an optional `clarification`.
2. `operations` resolves every figure: magnitude words, period conversion, and relative changes against the stored value.
3. `profile_ops.stage` / a `ChatGoalDraft` HOLD the result; nothing is written.
4. The reply reads it back and asks for a yes (`answer_formatter`, `gather` mode).
5. On confirmation: `profile_ops.commit` + `goal_builder.commit` + staged deletions, all in one pass, then `downstream.fire` over the audit rows they produced.

Each step reports through `planning_audit` — the same `chat_ai_module_runs` surface every other AI module uses, under `module="financial_planning"`, with `reason` in `read` / `staged` / `write` / `undo`.

## Files
- **services/planning_extractor.py** — the GATEWAY to `AI_Agents.response_extractor`, and the only place that imports it. Makes no LLM call itself: it builds the field catalogue, calls the agent, and resolves the reported parts into values via `operations`.
- **services/operations.py** — `Operation`, and every multiplication the model is not allowed to do. Pure functions, no I/O.
- **services/profile_ops.py** — CRUD on registry fields, through `profile.services.profile_write_router`.
- **services/goal_ops.py** — CRUD on `goals`, plus the whole-row snapshot that makes a delete undoable.
- **services/goal_builder.py** — the multi-turn conversation that costs and finances a goal; slot-driven, not step-driven.
- **services/plan_context.py** — read-only view of what we already hold, so the conversation never asks for it.
- **services/planning_state.py** — cross-turn state: the open ask (and its staging area), the goal draft, the audit trail.
- **services/downstream.py** — the table→effect map: what to re-run, keyed on what actually changed. `fire()` returns a `FireReport` covering every effect, run or skipped, with the columns that triggered each.
- **services/planning_audit.py** — the decision trail: what we understood, which table each value resolved to and why, what changed from what, and which effects that set off.
- **services/privacy.py** — what may reach a model, and in what shape.
- **models/** — `ChatPlanningAsk`, `PlanningWrite`, `ChatGoalDraft`.

## Gotchas & invariants
- **The LLM call lives in `AI_Agents/src/response_extractor/`, not here.** Agents own prompts and model calls; domains own CRUD, persistence and the call into the agent. The agent cannot import `app`, which is why the field catalogue is passed IN and why it never learns which table a field lives in.
- **Nothing is written directly.** Values are staged on the open ask, goals on a draft, and DELETIONS are staged too; a write happens only after an explicit yes (`planning_module_service._commit_everything` is the one writer). A figure parsed out of prose is our reading of what they said until they agree it is right.
- **The model does no arithmetic.** Every number comes back as (amount, magnitude, period) or as a relative INSTRUCTION, and `operations` multiplies it out. Asked to annualise "2.4 lakh a month", Haiku returned a figure a crore out at 0.95 confidence — a digit-count slip looks exactly as confident as a correct answer (`operations.to_stored_value`).
- **A relative change with nothing on file raises `NoBaseline`** and becomes a question. A percentage of an unknown figure is not a figure, and inventing a starting point puts a number in the plan neither party chose.
- **The extractor never sees a stored value.** It gets field NAMES, units and questions, and the customer's own goal labels — never their income, savings or date of birth. That is why relative math is resolved on this side (`privacy`).
- **Downstream work is keyed on the audit rows, not on the conversation** (`downstream.fire`). A risk input moving re-scores risk; a plan input or a goal moving retires the cached projection; a turn that changed nothing runs nothing. `_PLAN_INPUT_FIELDS` mirrors what `cashflow…input_builder` reads — if that builder starts reading another column, add it here or a plan gets served from a cache that predates its own input.
- **The projection requirement is `financial_planning_projection`, NOT the intent name.** The intent covers creating a goal (needs a cost and a date) and testing the plan (needs their income); gating at the top asked a customer for their salary before it would let them describe a wedding. `flow_financial_planning._project` applies it at the point of running the engine.
- **An ambiguous goal reference is a question, never a pick** (`goal_ops.resolve_ref` returns `None` on 0 or 2+ matches). Editing the wrong goal is worse than asking which one.
- **Table names predate the merge and are unchanged** — `chat_profile_capture_runs` backs `ChatPlanningAsk`, `profile_field_writes` backs `PlanningWrite` (and now also records goal changes with `table_name='goals'`). Renaming would need a migration path this repo does not have; see `app/core/database.py` `apply_postgres_schema_patches`.
- **Commit-free.** The chat router owns the transaction; `run_turn` never commits. The router endpoints DO commit — they are their own request.
- **`staged` and `write` are separate telemetry rows on purpose.** A value understood but never confirmed leaves a `staged` row and NO `write` row; that pair is how you tell "the customer declined" from "we dropped it". A turn that changes nothing still emits `read`, so silence in the log means the turn never reached the module.
- **The trail records the resolution, not just the value.** The extractor never names a table — it names a registry key, and the registry decides where that key lives. So every logged operation carries `field_key -> table.column`, which is the answer to "why was that table written". Goal operations deliberately carry no table: they do not go through the registry, and claiming one would be a lie.
- **The stored utterance is redacted** (`planning_audit.log_read`). The trail must not become a second copy of the identifiers `privacy` exists to strip out of the prompt.
- **Goal rows are condensed in the LOG LINE, whole in the PAYLOAD.** A goal carries all its columns in `previous_value` — that is what makes a delete undoable, and what would make the line unreadable.

## Testing
- `.venv/Scripts/python -m pytest app/domains/financial_planning -q`. The suite is deliberately LLM-free: the arithmetic, the redaction, the effect map and the reference matching are all pure.

## Don't read
- `__pycache__/`, `tests/`.
