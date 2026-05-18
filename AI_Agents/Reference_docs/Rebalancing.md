# Portfolio Rebalancing — Engine Thesis

*Engine version 1.0.0. This document explains the rebalancing engine that lives in `AI_Agents/src/Rebalancing/`. All thresholds, rules, and tax rates below match the live code as of May 2026 and reflect the FY25-26 equity tax regime. The companion technical specs (`input_parameter_spec.md`, `logical_flow.md`, `output_spec.md`) sit alongside the code in `Rebalancing/Reference_docs/` and are the source of truth for implementation detail.*

## 1. Why we rebalance

A rebalance is not a market call. Markets move, contributions arrive, some funds drop out of our recommended list, and the actual portfolio drifts away from the goal-based allocation we set up for you. Rebalancing brings the portfolio back to the planned shape — with a deliberate bias against unnecessary trading and a strict eye on the tax bill.

Three principles drive every decision the engine makes:

- **Stay within the planned shape.** Each fund has a target weight derived from your goals and risk profile; the engine measures the gap between today's holdings and that target.
- **Don't trade for trading's sake.** Small drifts are ignored by design — only gaps wide enough to matter trigger an action. Friction and tax cost are real, and we don't pay them for cosmetic alignment.
- **Realise gains carefully.** When we do sell, we sell the tax-cheap units first and respect any STCG headroom you've signalled for the year.

## 2. When a rebalance runs

The rebalance is event-based: it runs when an advisor asks for it, with the client's current holdings and the latest goal-based allocation as inputs. There is no automatic monthly or quarterly cadence in v1. The engine itself is sync, pure-Python, and DB-free; it produces a recommendation that an advisor reviews, optionally approves, and only then routes to execution.

## 3. How each fund is sized

Each `asset_subgroup` in your plan maps to one or more recommended funds, ordered by rank — rank 1 is the primary pick, ranks 2 and 3 are alternates in the same sub-category. The engine sizes them in two moves.

**Per-fund concentration caps.** No single fund is allowed to hold more than 20% of the total corpus when it sits in a sub-category mapped to the wider cap, or more than 10% otherwise. The wider-cap list today contains "Multi Cap Fund" and "Multi Asset Allocation" — broader-mandate categories where a higher single-fund concentration is acceptable. The set is a configurable lookup, easy to extend when the policy view changes.

**Spillover across ranks.** When the rank-1 fund's target exceeds its cap, the overflow is routed to rank-2 in the same asset subgroup; if rank-2 also caps out, it spills to rank-3. The walk is by rank within the asset subgroup, not within a sub-category — so an overflow from a rank-1 fund can land in a different sub-category's rank-2, whichever next rank still has cap headroom. The walk continues until the demand fits or we run out of ranks. If overflow can't be absorbed even at the last rank, the engine flags it as an `unrebalanced_remainder` warning rather than silently dropping it.

Sizing is purely a redistribution step: every potential overflow target already exists in the input (carrying a target of zero), and the engine only redistributes amounts across the existing slots — it never invents new fund rows.

## 4. What we change vs leave alone

After sizing, the engine joins the targets to your present holdings on ISIN and labels every fund with one of four actions: hold, top up, trim, or exit.

**The 10% worth-to-change threshold.** A drift is acted upon only when the absolute gap between target and present is at least 10% of the larger of the two amounts. Below that, the fund is left alone — small drifts don't justify the friction and tax cost of a trade. The threshold is a configuration knob (`REBAL_MIN_CHANGE_PCT`) and can be tightened or loosened without code changes.

**Forced exits override the threshold.** Two situations turn a fund into a must-sell regardless of how small the drift looks:

- **Not on the recommended list.** An ISIN you currently hold doesn't appear in the rank table for any sub-category — typically because our research team has replaced it or it has failed an ongoing screen. The engine tags it "BAD", gives it a target of zero, raises a warning so the advisor sees the rationale, and earmarks it for full exit.
- **Rating below the floor.** The fund's quality rating has dropped below 5 (the exit-floor rating). We hold a quality bar for funds in the portfolio; falling through it is a trigger to leave, even if the drift is otherwise within tolerance.

Funds that pass the threshold and aren't forced out stay put — no trade, no tax event, no exit-load drag. The engine doesn't emit rows for these funds, so the recommendation lists only the funds that are actually changing.

## 5. Tax-aware sell ordering

When the engine has to sell — to trim an over-allocated fund, to exit a dropped or low-rated one, or to fund a buy elsewhere — the order in which units are sold is what controls the tax bill. The ladder is the same in every run, applied lot by lot:

1. **Long-term lots first.** Equity lots held ≥ 12 months and debt fund-of-fund lots held ≥ 24 months. LTCG is taxed at 12.5% above a ₹1,25,000 annual exemption, so these are typically the cheapest units to liquidate.
2. **Realised losses next.** Short-term and long-term lots whose value has fallen below cost. Selling these crystallises a loss that can offset gains, either inside the same run or carried forward.
3. **Short-term lots outside the exit-load window.** STCG is taxed at 20%, but at least there's no exit-load drag on top.
4. **Short-term lots still inside the exit-load window.** The most expensive units to sell — both STCG and exit-load apply — and so the last to go.

The ladder is applied within both buckets the engine maintains: forced sells (BAD funds and low-rated funds, where we exit fully but in a tax-cheap order) and optional sells (over-allocated funds, where we also use the largest absolute drift as a tie-break). The net effect is simple to state: a BAD or low-rated fund is never kept around to save tax, but among funds we *could* sell, the tax-cheap ones always go first.

## 6. The two-pass trade plan

**Pass 1 — under the STCG offset budget.** If you've signalled a maximum STCG you're willing to realise this year (the `stcg_offset_budget`), the engine walks sells in priority order and stops adding new STCG once that budget is hit. Demand that can't be met under the budget is recorded as `undersell_due_to_stcg_cap` — not lost, just deferred to Pass 2.

v1 also assumes the rebalance is funded entirely from sells: every rupee bought has to come from a rupee sold. If the total demanded buys exceed total allowed sells, buys are scaled down proportionally and the shortfall is recorded as an underbuy. The engine never invents cash.

**Pass 2 — top up using carryforward losses.** Carryforward losses from prior years, plus any losses already realised in Pass 1, form a loss-offset budget. The engine takes the demand that was blocked by the STCG cap and converts as much of it as the loss budget can absorb into actual sells. This is how losses are put to work: they don't reduce taxes on past returns, but they unlock additional rebalancing without raising the current tax bill.

## 7. What this engine doesn't do (yet)

- **Closed-system assumption.** v1 assumes the rebalance is funded entirely from sells (Σ buys = Σ sells). Inflows and outflows are not modelled yet — clients adding fresh capital or withdrawing for a goal need a separate allocation step before this engine runs.
- **No advisor manual override.** The original spreadsheet had a column where the advisor could adjust the engine's auto target (e.g. partial-exit instead of full liquidation). v1 doesn't expose this; the engine's output is taken as-is, and any manual override has to be applied downstream.
