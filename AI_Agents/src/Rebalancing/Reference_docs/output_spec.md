# Rebalancing Module — Output Spec

## Context

This document closes the loop opened in [`input_parameter_spec.md`](input_parameter_spec.md) and [`logical_flow.md`](logical_flow.md). It specifies:

1. **Internal view** — full audit trail emitted by `step6_presentation`. Source of truth.
2. **Customer view** — flattened, presentation-ready shape exposed to the end user. Derived on read.
3. **Persistence** — exact JSON shape stored in `RebalancingRecommendation.recommendation_data`.
4. **API contract** — what `POST /rebalancing/compute` returns and what `GET /rebalancing/recommendations/{id}` returns to each audience.

Every field below is named to match the Pydantic models in [`models.py`](logical_flow.md#per-step-fundrow-models-decision-10-explicit-growing-by-inheritance) so reading docs ↔ code is mechanical.

---

## Layer 1 — Internal view (full audit trail)

Produced by `step6_presentation.apply(...)`. This is what the engine returns and what gets persisted verbatim.

**Pydantic model:** `RebalancingComputeResponse` in `AI_Agents/src/Rebalancing/models.py`.

```python
class RebalancingComputeResponse(BaseModel):
    rows: list[FundRowAfterStep5]      # one row per (asset_subgroup, sub_category, recommended_fund, rank)
    totals: RebalancingTotals
    metadata: RebalancingRunMetadata
    warnings: list[RebalancingWarning] = []
```

### `rows` — one row per `(asset_subgroup, sub_category, recommended_fund, rank)`

The full `FundRowAfterStep5` shape (defined in `logical_flow.md`). Includes:

- **Identity:** `asset_subgroup`, `sub_category`, `recommended_fund`, `isin`, `rank`
- **Targets across the pipeline:** `target_amount_pre_cap` (= `allocation_1`), `final_target_amount`, `holding_after_initial_trades`, `final_holding_amount`
- **Comparison:** `present_allocation_inr`, `diff`, `exit_flag`, `worth_to_change`
- **Tax & exit-load classification:** `stcg_amount`, `ltcg_amount`, `exit_load_amount`, `st_value_inr`, `lt_value_inr`
- **Initial trades (step 4):** `pass1_buy_amount`, `pass1_underbuy_amount`, `pass1_sell_amount`, `pass1_undersell_amount`, `pass1_sell_lt_amount`, `pass1_realised_ltcg`, `pass1_sell_st_amount`, `pass1_realised_stcg`, `stcg_budget_remaining_after_pass1`, `pass1_sell_amount_no_stcg_cap`, `pass1_undersell_due_to_stcg_cap`, `pass1_blocked_stcg_value`
- **Loss-offset top-up (step 5):** `stcg_offset_amount`, `pass2_sell_amount`, `pass2_undersell_amount`

Including BAD-fund rows (held but not recommended) — `is_recommended = False`, target = 0.

### `totals`

```python
class RebalancingTotals(BaseModel):
    total_buy_inr: Decimal
    total_sell_inr: Decimal
    net_cash_flow_inr: Decimal              # ≈ 0 in v1 (closed system); reported for cross-check
    total_stcg_realised: Decimal
    total_ltcg_realised: Decimal
    total_stcg_net_off: Decimal             # losses applied
    total_tax_estimate_inr: Decimal         # uses C2/C3 + C1 exemption
    total_exit_load_inr: Decimal
    unrebalanced_remainder_inr: Decimal     # any rank-N overflow that didn't fit
    rows_count: int
    funds_to_buy_count: int
    funds_to_sell_count: int
    funds_to_exit_count: int
    funds_held_count: int                   # worth_to_change = False
```

### `metadata`

```python
class RebalancingRunMetadata(BaseModel):
    computed_at: datetime                   # UTC
    engine_version: str                     # semver, bumped manually on logic change
    request_corpus_inr: Decimal             # B1 at compute time
    knob_snapshot: KnobSnapshot             # Decision 11 — see below
    request_id: UUID                        # for log correlation

class KnobSnapshot(BaseModel):
    multi_fund_cap_pct: float
    others_fund_cap_pct: float
    rebalance_min_change_pct: float
    exit_floor_rating: int
    ltcg_annual_exemption_inr: Decimal
    stcg_rate_equity_pct: float
    ltcg_rate_equity_pct: float
    st_threshold_months_equity: int
    st_threshold_months_debt: int
    multi_cap_sub_categories: list[str]
```

`knob_snapshot` captures every value from buckets A and C **as resolved at compute time** (after env-var overrides). Lets us replay a recommendation exactly as it was computed even if defaults change later.

### `warnings`

```python
class RebalancingWarning(BaseModel):
    code: WarningCode                       # enum: UNREBALANCED_REMAINDER, STCG_BUDGET_BINDING,
                                            #       NO_HOLDINGS_FOR_RECOMMENDED_FUND,
                                            #       BAD_FUND_DETECTED, ...
    message: str
    affected_isins: list[str] = []
```

Step 1 raises `UNREBALANCED_REMAINDER` when overflow can't fit (sheet row 325 caveat). Step 2 raises `BAD_FUND_DETECTED` per BAD ISIN. Step 4 raises `STCG_BUDGET_BINDING` if D1 is the binding constraint. The engine **never silently drops** information — every undersold/overflow gets a warning row.

---

## Layer 2 — Customer view (presentation)

Built on read by `app/services/rebalancing/customer_view_adapter.py` (Decision 12). **Never persisted** — derived from Layer 1 every time a customer asks. This decouples the customer presentation from engine versions.

**Pydantic model:** `CustomerRebalancingView` in `app/schemas/rebalancing.py`.

```python
class CustomerRebalancingView(BaseModel):
    headline: CustomerHeadline
    trade_list: list[CustomerTradeAction]   # one row per recommended action; HOLD rows dropped
    summary: CustomerSummary
    advisor_note: str | None = None         # advisor-edited note attached at approval time
```

### `trade_list` — `mutual_fund × sub_category × buy/sell` (Decision per user request)

```python
class CustomerTradeAction(BaseModel):
    sub_category: str                       # e.g. "Multi Cap Fund"
    recommended_fund: str                   # e.g. "ICICI Prudential Multicap Fund - Growth"
    isin: str
    action: Literal["BUY", "SELL", "EXIT"]  # HOLD rows are filtered out
    amount_inr: Decimal                     # absolute, always positive
    rationale: str                          # one short sentence
```

**Rationale strings** come from a small constant map in `app/services/rebalancing/rationales.py`, keyed by reason code:

| Reason code | Triggered when | Sample rationale string |
| --- | --- | --- |
| `add_to_target` | `diff > 0 AND worth_to_change AND is_recommended` | "Top up to bring this fund's share back to your target allocation." |
| `trim_over_target` | `diff < 0 AND worth_to_change AND is_recommended AND fund_rating ≥ exit_floor_rating` | "Trim back — this fund is over its allocation cap." |
| `exit_bad_fund` | `is_recommended = False` | "Exit — this fund is not part of your recommended portfolio." |
| `exit_low_rated` | `is_recommended = True AND fund_rating < exit_floor_rating` | "Exit — this fund's rating has dropped below our threshold." |
| `cap_spill_buy` | rank ≥ 2 with `pass1_buy_amount > 0` | "Allocate to this alternate fund — your primary pick is at its per-fund cap." |

Asset_subgroup is **deliberately omitted** from the customer view — it's an internal categorisation, not a customer concept.

### `headline`

```python
class CustomerHeadline(BaseModel):
    portfolio_drift_summary: str            # e.g. "Your portfolio has drifted by ₹X across N funds."
    proposed_action_summary: str            # e.g. "We recommend Y trades totalling ₹Z."
```

### `summary`

```python
class CustomerSummary(BaseModel):
    total_buy_inr: Decimal
    total_sell_inr: Decimal
    estimated_tax_inr: Decimal
    estimated_exit_load_inr: Decimal
    funds_to_buy_count: int
    funds_to_sell_count: int
    funds_to_exit_count: int
    # Internal counters (rows_count, funds_held_count, unrebalanced_remainder, etc.) NOT exposed.
```

### Adapter contract

```python
# app/services/rebalancing/customer_view_adapter.py

def to_customer_view(
    response: RebalancingComputeResponse,
) -> CustomerRebalancingView:
    """Pure: internal_view → customer_view. No DB calls."""
```

Filtering rules:
- Drop rows where `worth_to_change = False AND not exit_flag` (HOLD rows).
- Collapse rank-1, rank-2, rank-3 of the same `sub_category` into separate `trade_list` entries (each fund is shown by name).
- Aggregate `pass1_buy_amount + pass2_sell_amount`-style step-internal fields into a single `amount_inr`. Sign convention: amount is **always positive**; direction lives in `action`.

---

## Layer 3 — Persistence

### Storage target

[`RebalancingRecommendation`](../../../../app/models/rebalancing.py) ORM table — already exists. Per Decision 11, the **full internal view** is stored in `recommendation_data` (JSONB).

### JSONB shape

```jsonc
{
  "schema_version": "1.0",
  "rows": [
    {
      "asset_subgroup": "multi_asset",
      "sub_category": "Multi Cap Fund",
      "recommended_fund": "ICICI Prudential Multicap Fund - Growth",
      "isin": "INF109K01613",
      "rank": 1,
      "target_amount_pre_cap": 3614300,
      "final_target_amount": 1600005,
      "holding_after_initial_trades": 1600005,
      "final_holding_amount": 1600005,
      "present_allocation_inr": 2000005,
      "diff": -400000,
      "exit_flag": false,
      "worth_to_change": true,
      "stcg_amount": -87,
      "ltcg_amount": 5357,
      "exit_load_amount": 700001.75,
      "pass1_buy_amount": 0,
      "pass1_sell_amount": 400000,
      "pass2_sell_amount": 0,
      "pass1_sell_lt_amount": 163650,
      "pass1_sell_st_amount": 190950,
      "pass1_realised_ltcg": 5357,
      "pass1_realised_stcg": -87,
      "stcg_budget_remaining_after_pass1": 0,
      "stcg_offset_amount": 0,
      "is_recommended": true
      // …all FundRowAfterStep5 fields…
    }
    // …one entry per recommended fund + per BAD fund…
  ],
  "totals": {
    "total_buy_inr": 0,
    "total_sell_inr": 2219999,
    "net_cash_flow_inr": -2219999,
    "total_stcg_realised": -87,
    "total_ltcg_realised": 5357,
    "total_tax_estimate_inr": 670,
    "total_exit_load_inr": 1438336,
    "unrebalanced_remainder_inr": 0,
    "rows_count": 32,
    "funds_to_buy_count": 0,
    "funds_to_sell_count": 5,
    "funds_to_exit_count": 1,
    "funds_held_count": 26
  },
  "metadata": {
    "computed_at": "2026-04-25T10:15:00Z",
    "engine_version": "1.0.0",
    "request_corpus_inr": 8000025,
    "request_id": "550e8400-e29b-41d4-a716-446655440000",
    "knob_snapshot": {
      "multi_fund_cap_pct": 20.0,
      "others_fund_cap_pct": 10.0,
      "rebalance_min_change_pct": 0.10,
      "exit_floor_rating": 5,
      "ltcg_annual_exemption_inr": 125000,
      "stcg_rate_equity_pct": 20.0,
      "ltcg_rate_equity_pct": 12.5,
      "st_threshold_months_equity": 12,
      "st_threshold_months_debt": 24,
      "multi_cap_sub_categories": ["Multi Cap Fund"]
    }
  },
  "warnings": [
    { "code": "BAD_FUND_DETECTED", "message": "Held fund INF…BAD is not in the recommended set.", "affected_isins": ["INF…BAD"] }
  ]
}
```

### Schema versioning

`schema_version: "1.0"` lives at the top of the JSON. Bump on any breaking change to row structure. Older recommendations stay readable because the adapter pattern-matches on `schema_version` and dispatches to the right reader.

---

## API contract

The existing router stub [app/routers/rebalancing.py](../../../../app/routers/rebalancing.py) is extended with:

### `POST /rebalancing/compute` — Compute and persist a fresh recommendation

**Request body:** `RebalancingComputeRequestApi` — thin wrapper around the engine's `RebalancingComputeRequest`. The router calls:
1. `rebalancing_input_builder.build_request(user_id, db) → RebalancingComputeRequest`
2. `pipeline.run_rebalancing(request) → RebalancingComputeResponse`
3. Persists to `RebalancingRecommendation` (status = `pending`).
4. Returns the **internal view** (advisor-facing endpoint).

**Response 200:** `RebalancingComputeResponse` (Layer 1 in full).

### `GET /rebalancing/recommendations/{id}` — Read a stored recommendation

Query param `?view=internal|customer` (default `customer` for end users; `internal` requires advisor scope).

- `view=internal`: returns `recommendation_data` JSON verbatim.
- `view=customer`: passes `recommendation_data` through `customer_view_adapter.to_customer_view()` and returns `CustomerRebalancingView`.

Auth: customer scope can read only their own portfolio's recommendations and only `view=customer`.

### `PATCH /rebalancing/recommendations/{id}/status` — Approve / reject / mark executed

Already in the stub; unchanged.

---

## Where the views are produced (summary)

```
                  AI_Agents/src/Rebalancing/
                  └── pipeline.run_rebalancing()
                          │
                          │  produces
                          ▼
                   RebalancingComputeResponse  ──── stored as JSONB ────► RebalancingRecommendation
                   (Layer 1: internal view)                                     │
                          │                                                     │
                          │ on read (GET ?view=customer)                        │
                          │                                                     │
                          ▼                                                     │
       app/services/rebalancing/customer_view_adapter.to_customer_view()        │
                          │                                                     │
                          ▼                                                     ▼
                  CustomerRebalancingView                              advisor UI / audit log
                   (Layer 2: customer view)                            (Layer 1)
                          │
                          ▼
                    customer UI
```

---

## Testing

Add to the existing [`Testing/`](logical_flow.md#testing-strategy) directory:

| Test file | Asserts |
| --- | --- |
| `test_step6_presentation.py` | Engine output matches expected `RebalancingComputeResponse` shape; totals are arithmetic-consistent (Σ row buys = `total_buy_inr`); `knob_snapshot` reflects current env. |
| `test_customer_view_adapter.py` | Internal view → customer view filters HOLDs, drops `asset_subgroup`, attaches the right rationale string per reason code. |
| `test_persistence_roundtrip.py` | Insert internal view JSONB → read back → `to_customer_view()` → assert equality with adapter applied to original Pydantic. Catches JSON-roundtrip corruption (e.g. Decimal → string). |

Adapter and persistence tests live under `app/services/rebalancing/tests/` (backend tests) — not under `AI_Agents/src/Rebalancing/Testing/` (engine tests).

---

## Critical files (additions to logical_flow.md's list)

After the engine's 17 files, add:

18. `AI_Agents/src/Rebalancing/models.py` — extend with `RebalancingTotals`, `RebalancingRunMetadata`, `KnobSnapshot`, `RebalancingWarning`, `WarningCode` enum.
19. `app/schemas/rebalancing.py` — extend with `CustomerRebalancingView`, `CustomerTradeAction`, `CustomerHeadline`, `CustomerSummary`, `RebalancingComputeRequestApi` wrapper.
20. `app/services/rebalancing/customer_view_adapter.py` — new.
21. `app/services/rebalancing/rationales.py` — new (reason-code → string map).
22. `app/services/rebalancing/tests/test_customer_view_adapter.py` — new.
23. `app/services/rebalancing/tests/test_persistence_roundtrip.py` — new.

---

## Decisions confirmed (this round, continuing the numbering)

13. **Three layers.** Engine emits internal view; adapter derives customer view on read; persistence stores internal view + knob snapshot.
14. **Customer trade-list grouping.** `mutual_fund × sub_category × buy/sell` (no `asset_subgroup` exposed). One entry per acted-upon fund.
15. **Warnings, not silent drops.** Step 1 overflow, BAD-fund detection, and STCG-budget-binding are surfaced as structured warnings on the response.
16. **JSON schema versioning.** `recommendation_data` carries `schema_version`; adapter dispatches on it.
17. **Rationale strings centralised.** `app/services/rebalancing/rationales.py` is the single source — easy to translate, easy to A/B test wording.
