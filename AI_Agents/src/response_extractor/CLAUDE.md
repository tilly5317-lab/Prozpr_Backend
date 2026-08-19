# AI_Agents/src/response_extractor/ — read one customer message into typed plan operations

Runs immediately after `intent_classifier` and does the job that agent deliberately does not: the classifier says WHICH service area a message belongs to; this reads WHAT is in it. Returns a list of typed operations (`target` × `verb`) plus the message's overall `kind` — no prose, no advice, no arithmetic.

## Entry / contract
- `extractor.py` exposes `ResponseExtractor(api_key=…).aextract(payload)` → `ExtractionResult`. LangChain + Claude Haiku, structured output, prompt caching on the static system block. Async-native, so a caller timeout really cancels the HTTP call.
- The caller supplies the field catalogue in `ExtractionInput.capturable_fields`. The agent has no registry, no database, and no idea where any value is stored — it reports a `field_key` and the app layer resolves it to a table.
- One app-layer owner: `app/domains/financial_planning/services/planning_extractor.py`. Nothing else may import this package.

## Files
- `models.py` — the enums (`Target`, `Verb`, `Magnitude`, `Period`, `MessageKind`, `GoalType`) and the I/O models (`ExtractionInput` / `ExtractionResult`).
- `prompts.py` — the static system prompt + `build_user_block`, which renders the per-turn half.
- `extractor.py` — the tool schema (`_LLMOutput`) and the pipeline.

## Gotchas & invariants
- **It does no arithmetic, and that is the whole design.** Figures come back as `amount` + `magnitude` + `period`; the caller multiplies. Asked to annualise "2.4 lakh a month" the model returned a figure a crore out at 0.95 confidence, and a digit-count slip is indistinguishable from a correct answer. A model cannot make that mistake about arithmetic it is never asked to do (`prompts.py`, DO NO ARITHMETIC).
- **A relative change is reported as an INSTRUCTION, never a result.** "Up 20%" comes back as `verb=adjust` + `change={direction, pct}`. The agent is never told the current figure, so it cannot leak one and cannot guess one; the caller resolves it against the database (`models.Change`).
- **`ExtractionInput` carries no stored VALUES** — only which fields exist and what units they are in. That is the privacy boundary, enforced by the input type rather than by prompt wording. A drift test asserts the field set.
- **Every vocabulary is a `Literal` in `_LLMOutput`**, so the Anthropic tool schema enforces it at the API level and the model physically cannot emit an unknown verb. Keep them in sync with the enums in `models.py`; the drift test (`app/domains/financial_planning/tests/test_response_extractor_schema.py`) fails loudly otherwise.
- **`cost` vs `cost_estimate` is load-bearing.** `cost` is only a number the customer said; `cost_estimate` is the agent's own guess for something specific enough to price. Downstream tells the customer whose number it is, so mixing them presents our guess as their statement (`prompts.py`, GOALS).
- **`temperature=0` is pinned as a literal** — unset applies the API default of 1.0. A repo-wide scan (`test_temperature_is_pinned.py`) enforces this.
- Field validators scrub stray closing-tag debris (`"true</is_follow_up>"`) that Anthropic's tool serializer occasionally leaks into values — the same defence `intent_classifier` carries.

## Don't read
- `__pycache__/`.
