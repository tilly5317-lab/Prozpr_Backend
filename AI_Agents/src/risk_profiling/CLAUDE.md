# AI_Agents/src/risk_profiling/ — score a client's risk profile

Deterministic Python scoring (risk capacity, OSI, savings-rate adjustment, clamp, effective risk score) plus a Claude Haiku summary paragraph. Produces `effective_risk_score` + supporting fields consumed downstream via allocation modules' `AllocationInput`.

## Entry / contract
- `main.py` exposes `risk_profiling_chain` (LCEL: `compute_all_scores` → LLM summary). Scoring is fully deterministic; only the trailing summary step calls the LLM.
- Output is an open `dict` (keys `step_name`, `inputs`, `calculations`, `output`), not a fixed model — the app layer indexes it by key and persists `calculations`/`output` as JSON.

## Files
- `main.py` — the LCEL chain.
- `scoring.py` — `compute_all_scores`; pure-Python scoring, no LLM.
- `willingness.py` — `compute_risk_willingness` (re-exported); scores the four-question willingness questionnaire, tolerating unanswered questions.
- `models.py` — `RiskProfileInput`. `prompts.py` — summary prompt + `RiskProfileSummary`.
- `dev_run.py` / `customer_test_data.py` — smoke test + canned profiles. `README.md` — human guide.

## Gotchas & invariants
- Numerics are pre-formatted before the summary call so the LLM never interprets sentinels: `savings_rate=None`→"N/A", `coverage`/`debt` ≥ `999.0`→"N/A (no financial assets)" (`main.py` `_generate_summary`).

## Don't read
- `__pycache__/`.
- `customer_test_output.json` / `.csv` — captured run artifacts, not schemas.
