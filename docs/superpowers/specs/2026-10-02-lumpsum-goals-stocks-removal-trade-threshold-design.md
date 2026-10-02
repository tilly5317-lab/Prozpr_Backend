# Lump sum funds goals in full · stocks leave the engines · new trade threshold

*Design approved by Amoul on 2026-10-02 and revised the same day after an independent audit. Branch: `rebal_logic_changes_0920`.*

This covers three independent changes. Each one either simplifies existing logic or retunes it; none adds a new mechanism.

---

## 1. A lump sum fills near-term goals in full, then goes long-term

### Today

SIP and lump sum both put goal money first, into one goal subgroup (`short_debt` or `arbitrage`), and split the rest long-term. They differ only in how large the goal share is:

- **SIP:** the share is `GoalFunding.monthly_sip_to_goals`.
- **Lump sum:** the share is `GoalFunding.from_corpus`, capped at the deploy amount (`ainv_engine/input_builder.py::goal_share_for`).
  - The lump-sum practical-allocation (PAA) run gets `monthly_sip=None` (`ainv_engine/service.py:252`), so it uses the profile SIP.
  - `from_corpus` is therefore only the part the profile SIP cannot reach by each goal's date.
  - Example: a ₹6L goal due in 12 months, a ₹50k SIP and a ₹5L lump sum. Today ₹0 of the lump sum goes to the goal.

### Change

The lump-sum PAA run passes `monthly_sip=0.0` instead of `None`. The SIP path is unchanged.

```python
monthly_sip=deploy_amount_inr if cadence is Cadence.SIP_MONTHLY else 0.0,
```

With the SIP at zero, the step-2 waterfall gives each short-term goal, nearest first, its full remaining need: `amount_needed_fv − from_holdings`, capped at the remaining corpus.

- `from_corpus` becomes the total gap. `allocated_amount` becomes the goals' future value, capped at the corpus.
- `goal_share_for` is unchanged: it returns `from_corpus` capped at the deploy amount.
- Whatever is left of the lump sum goes to the long-term deficit-fill, as today.

**Why not change `goal_share_for` instead?** The PAA run's long-term column would still assume the SIP funds the goals. The lump-sum facts (`goal_row`: ideal = `allocated_amount`, current = held, `service.py:387-393`) would then disagree with what we buy. Zeroing the SIP keeps the plan and the buy consistent.

**Scope.**
- Only the lump-sum PAA run changes. Rebalancing, SIP, the ideal plan and chat keep the profile SIP.
- The lump-sum PAA run *is* persisted to `practical_asset_allocation_runs` (`service.py:461`). That is fine here, with one exception: the preferences resolver's `_current_mixes` reads the latest row as the customer's current mix, and a lump-sum row now leans further toward debt. Amoul decided to leave this. Preference lump sums are handled on the preferences branch.
- `AINV_ENGINE_VERSION` goes `ainv-3.5.0` → `ainv-3.6.0` (`service.py:132`). This bump also covers change 2.

**Accepted (Amoul, 2026-10-02): a running SIP can double-fund a goal.**
- Nothing re-sets a running SIP automatically. There is no SIP review scheduler, and an executed SIP is a fixed purchase plan.
- So the SIP keeps paying its goal share until the customer re-sets it. In the example above, the goal could receive ₹5L from the lump sum plus up to ₹6L from the SIP.
- Once the money is held, the surplus counts as long-term holdings, and a later rebalance moves it into the long-term mix.
- Cost: the money sits in debt for a while, and gains on the switched debt are taxed.

**Step-2 shortfall message.** `step2_short_term.py:128-139` says "Your current savings plus your monthly investment…" even when the SIP is 0. The ideal plan shows this message to customers (`aa_engine/service.py:218-224`), including customers with no SIP. Replace it with one sentence that doesn't depend on the SIP: "Your short-term goals won't be fully funded by their dates — the gap is ₹X. You can close it by investing more each month or by reducing some of your goals."

**Preference customers** are unchanged. Carve-outs are suspended, `goal_funding` is None, and the goal share is 0.

---

## 2. Non-mutual-fund equity (direct stocks / PMS) is invisible to every engine

### Today

- **Practical (PAA) engine:**
  - It carries one scalar, `non_mf_equity_corpus`.
  - It applies a cap band of 33/50/60/75% of long-term equity, keyed on `financial_assets`.
  - It counts the capped part toward equity as a frozen `non_mf_equities` row and redeploys the excess (`excess_direct_stocks`).
- **Rebalancing:**
  - It turns that excess into a `SELL_DIRECT_STOCKS` action and into stock cash in step 4.
  - It deducts stocks in the 1-vs-2-funds test.
  - In production it always receives 0, because its builder uses an MF-only corpus.
- **Additional investment:** it excludes the frozen row and subtracts stocks from `investable_corpus_inr`.
- **Live sources:**
  - **Direct:** the only live non-zero source is the lump-sum `CorpusPin`, built from `HoldingsSnapshot.non_mf_equity_inr`.
  - **Indirect:** `aa_engine/input_builder.py::pick_total_corpus` takes the max of three candidates, one of which is the primary `Portfolio.total_value`. For Account Aggregator (AA) users that total includes demat equity (`simbanks_service.py:1081`).

### Change: stocks are never in an engine corpus

**`portfolio/services/holdings_snapshot.py::aggregate_holdings`**
- Skip rows whose `instrument_type` is in the direct-equity set instead of bucketing them to `non_mf_equities`.
- Rename the set to public `EQUITY_INSTRUMENT_TYPES` so other modules can reuse it.
- Delete the `non_mf_equity_inr` property and `_SUBGROUP_NON_MF_EQUITIES`.
- Effect: the lump-sum `CorpusPin` total and `mf_corpus` exclude stocks.
- The snapshot's only other consumer is the PAA short-term-holdings read, which never counted stocks anyway.

**`aa_engine/input_builder.py::pick_total_corpus`**
- When the primary portfolio has holdings, its candidate becomes `snapshot_from_holdings(primary.holdings).total_inr`, which excludes stocks.
- Otherwise it falls back to `total_value`.
- **Why not `total_value − stocks`:** a CAMS upload rewrites `total_value` as MF-only (`cams_cas_ingest.py:656`) but deletes only `mutual_fund` holding rows (`:675-681`). Stock rows from an earlier AA sync survive, so subtracting them would understate the corpus, possibly below zero.
- Holdings are preloaded on every production path:
  - `user_context_loader.py:31-34` loads portfolios, holdings and fund metadata.
  - `expire_on_commit=False` keeps them available after commit.

**Rebalancing:** no corpus change. It already sums held MF rows only.

### Delete: the stock machinery

**`practical_asset_allocation/pipeline.py`**
- Input fields `non_mf_equity_corpus` and `max_non_mf_equity_pct_client_input`.
- The band constants and `_asset_banded_max_non_mf_equity_pct`.
- The cap maths in `_run_practical_long_term`: `non_mf_equity_actual` and `excess_direct_stocks`.
- The slider's share pool (`total_equity_pool_for_shares` / `share_denominator`) stops adding `non_mf_equity_actual`, and `locked_amount` becomes ELSS only.
- The frozen `non_mf_equities` row in `_step5_aggregation_with_frozen`, and its mapping in `_build_asset_class_breakdown`.
- `CorpusBreakdown` fields: `non_mf_equity_input_inr`, `non_mf_equity_actual_inr`, `excess_direct_stocks_inr`, `max_non_mf_equity_pct_computed`, `non_mf_equity_cap_inr`. Also `lt_equities_amount_inr`, whose only purpose was to be the cap's denominator.
- **`mf_corpus` everywhere.** Once stocks are gone, every producer sets it equal to `total_corpus`, and the engine only echoes it. That covers `PracticalAllocationInput.mf_corpus`, `CorpusPin.mf_corpus`, `CorpusBreakdown.mf_corpus_inr`, the rebalancing builder's `"mf_corpus"` update, and the trace and debug keys. The persisted `mf_corpus` column stays, unwritten (NOT NULL, default 0), and joins the later drop migration.
- `_build_asset_class_breakdown`'s `extended_map` is a copy of `human_override.CLASS_OF`, so use `CLASS_OF` instead.
- The trace entries and `__init__.py` docstring.
- Keep: `elss_corpus` and `rebalancing_corpus`. `CorpusBreakdown` becomes `total_corpus_inr`, `elss_corpus_inr` and `rebalancing_corpus_inr`, and `CorpusPin` becomes `(total_corpus, elss_corpus)`.

**One frozen-subgroup set.** `human_override.FROZEN_SUBGROUPS = frozenset({"tax_efficient_equities"})` becomes the single definition. These all import it, and their private copies are deleted:
- Rebalancing's `_FROZEN_SUBGROUPS`;
- `allocation_snap._FROZEN`;
- the app's ainv `_EXCLUDE_SUBGROUPS`.

This adds no new dependency direction: Rebalancing and the app already import `practical_asset_allocation`.

**One fund-count rule.** `funds_per_subgroup(corpus)` lives in `Rebalancing/config.py` next to `SUBGROUP_FUND_COUNT_THRESHOLD_INR`, and both Rebalancing and ainv call it (ainv already imports `Rebalancing.config`). It replaces the two private `_funds_per_subgroup` copies.

**One direct-equity type set.** `holdings_snapshot.EQUITY_INSTRUMENT_TYPES` becomes public, and `allocation_rollup` imports it in place of its own `_DIRECT_EQUITY_ITYPES`.

**Trade fields.** `TradeAction.isin`, `sub_category` and `recommended_fund` become required `str`. Only `SELL_DIRECT_STOCKS` ever left them None, and the ORM columns are already NOT NULL.

**PAA chat markdown.** Delete the "Corpus split" line. With stocks gone, it repeats the corpus and an always-zero ELSS on that path.

**`asset_allocation_pydantic`**
- Delete `AllocationInput.financial_assets`; its only reader is the PAA band. Delete its setter too (`aa_engine/input_builder.py:266`).
- `equity_subgroup_slider.py` and `step4_long_term.py:445,459` keep their mechanics. Only the comments and docstrings that mention non-MF change, so that `locked_amount` reads as ELSS only.

**`practical_asset_allocation/allocation_snap.py`, `human_override.py`**
- Drop `non_mf_equities` from the frozen sets and from `CLASS_OF`.
- The shortfall copy names locked ELSS only.
- `preference_save_service.py` and `screen_preference_service.py` import `FROZEN_SUBGROUPS`, so they follow automatically. Only the comment at `screen_preference_service.py:46-47` needs editing.

**`practical_asset_allocation/services/paa_engine/`**
- `CorpusPin.non_mf_equity_corpus` (`input_builder.py:63,157,159,168`, plus the docstring at lines 8-12).
- The "non-MF equity" line in the `service.py` corpus-split markdown and trace.

**Rebalancing (`AI_Agents/src/Rebalancing/`)**
- `non_mf_equities` leaves `_FROZEN_SUBGROUPS`.
- `_funds_per_subgroup` and its caller test `total_corpus` on its own.
- Step 4 loses its `extra_cash_inr` parameter and the stock-cash feed into it.
- Step 6 stops emitting the `non_mf_equities` summary and `_sell_direct_stocks_action`.
- `SELL_DIRECT_STOCKS` leaves the `TradeAction.action` literal. Production never persisted it, because the app's `TradeAction` enum has only BUY/SELL/EXIT.
- Delete the `sell_excess_direct_stocks` rationale.
- Comments: `config.py:36-38,132` and `models.py:156,281,297-301,326`.

**Additional investment**
- App side:
  - `non_mf_equities` leaves `_EXCLUDE_SUBGROUPS` (`ainv_engine/input_builder.py`); ELSS stays.
  - `investable_corpus_inr = float(cb.total_corpus_inr)` (`service.py:276`), and the negative-rounding comment goes.
  - The sized-SIP pin stops passing `non_mf_equity_corpus=0.0` (`service.py:336`).
  - The `"non_mf_equities": "direct stocks"` label in `lumpsum_reasoning.py` goes.
- Engine comments: `additional_investment/models.py:64,83` and `pipeline.py:25`.
- `rebal_engine/input_builder.py:427-431`: the comment drops the "stocks default to 0" wording.

**Rebalancing chat:** `direct_stock_sale_inr` and `direct_stock_sale_indian` leave the facts pack (`rebal_engine/service.py`), and the matching line leaves the prompt (`rebal_engine/chat.py:513`).

### Keep

- **ELSS stays a frozen sleeve.** It is a mutual fund with a lock-in.
- **The cashflow engine keeps `equity_shares`.** It is a net-worth input, not allocation logic.
- **What-you-hold displays still count stocks as Equity**, because they describe actual holdings. These are `allocation_rollup.py`, the dashboard donut, `compute_current_asset_class_mix` and the rebalancing-router fallback.
- **The four stock columns on `practical_asset_allocation_runs` stay.** They are NOT NULL with default 0, so once the persist service stops writing them new rows get 0. No migration now; dropping them is a separate, later migration.
- **Ingestion is unchanged.** AA demat rows stay `instrument_type="equity"`.
- **Frontend:** `Prozpr_Frontend/src/pages/RebalanceExplanation.tsx:61` keeps a now-dead `sell_excess_direct_stocks` label mapping. It is harmless, and Amoul can remove it in the frontend repo.

### Accepted consequences (Amoul, 2026-10-02)

- **Stock-heavy customers will hold more total equity than the plan intends.** Their mutual-fund equity targets get no offset for the stocks they already hold.
- **A customer whose only holdings are stocks now looks like a customer with no holdings:**
  - `short_term_holdings_for_user` returns None.
  - For an AA customer whose only holdings are demat stocks, and who has no other corpus figure, the allocation's zero-corpus gate asks them to upload a CAMS statement (`aa_engine/service.py:536`).
- **AA stock holders get a smaller corpus for the ideal plan, SIP and preference runs.** More of them hit the ₹1Cr sizing rescue, and some drop from 2 funds to 1 at the ₹50L line.
- **`pick_total_corpus` now counts all holdings except stocks.** That includes AA bank balances, which sit in the snapshot's `unknown_inr`.
  - For AA-only customers this matches today, because `total_value` already includes bank balances (`simbanks_service.py:894`).
  - Customers who synced AA and later uploaded CAMS now get their bank balances counted too, the same as AA-only customers. Today CAMS's `total_value` leaves them out.
  - An AA demat summary remainder that has no holding row (`simbanks_service.py:1132-1142`) is dropped.
- **ETFs from demat without mutual-fund markers become invisible**, like stocks.
- **Two corpus candidates are user-declared and not filtered for stocks:** `inv.portfolio_value` and the profile's `financial_assets`. "Never in a corpus" is guaranteed only for the holdings-derived figures.

---

## 3. Trade threshold: min(1% of portfolio, 50% of fund value)

### Today

`Rebalancing/config.py:31` sets `REBALANCE_MIN_CHANGE_PCT = 0.10` (env `REBAL_MIN_CHANGE_PCT`). Per fund row:

```
worth_to_change = |target − present| ≥ 0.10 × max(target, present)   or exit_flag
```

- Step 2 applies it (`step2_compare_and_decide.py:48-50`).
- Step 2b re-gates with it after debt netting (`step2b_suppress_debt_switch.py:180-183`) and absorbs any residual that falls below the bar.

### Change

```
threshold = min(PORTFOLIO_PCT × request.total_corpus, FUND_PCT × max(target, present))
worth_to_change = |diff| ≥ threshold   or exit_flag
```

**Config:** `REBALANCE_MIN_CHANGE_PCT` is replaced by two fractions:
- `REBALANCE_MIN_CHANGE_PORTFOLIO_PCT = 0.01` (env `REBAL_MIN_CHANGE_PORTFOLIO_PCT`)
- `REBALANCE_MIN_CHANGE_FUND_PCT = 0.50` (env `REBAL_MIN_CHANGE_FUND_PCT`)

**Code:**
- A single helper, `min_change_threshold(scale, corpus) -> Decimal`, lives in `config.py` and is used by both step 2 and step 2b.
- It reads the two module globals at call time, so tests can monkeypatch `config`.
- Step 2 stops ignoring `request`.

**Definitions:**
- **"Portfolio"** is `request.total_corpus`, the held MF value in production. It is 0 only when every held value is 0, and then every target is 0 too.
- **"Fund value"** stays `max(target, present)`. New funds (present 0) and full exits (target 0) always pass.

**Unchanged:**
- the `exit_flag` bypass (exit rows are never netted in 2b);
- step 2b's absorption of sub-threshold residuals;
- step 2's zero-scale quirk, where `0 ≥ 0` evaluates true. It is harmless in step 4.

**`KnobSnapshot`:**
- `rebalance_min_change_pct` is replaced by `rebalance_min_change_portfolio_pct: Optional[float] = None` and `rebalance_min_change_fund_pct: Optional[float] = None`.
- They default to None, not 0.01/0.50, so that a snapshot persisted under the 10% rule doesn't parse as if it used the new knobs.
- Pydantic's default `extra="ignore"` drops the old key from those snapshots.
- Fixtures in `rebal_engine/tests` are updated.

**Version:** `ENGINE_VERSION` goes `1.14.0` → `1.15.0`. The release note covers this change and the stock removal from change 2.

**Effect on a ₹1Cr portfolio:**

| Fund value | Today must move ≥ | New must move ≥ |
|---|---|---|
| ₹30L | ₹3L | ₹1L |
| ₹5L | ₹50k | ₹1L |
| ₹1L | ₹10k | ₹50k |

At any portfolio size, the new bar is lower than today's for funds above 10% of the portfolio, and higher for funds below it. Small portfolios with concentrated funds will trade more readily: on a ₹5L portfolio the bar is at most ₹5k. That is accepted, with no rupee floor.

---

## Testing

**Baseline first.** Run the full suite before any change, then compare FAILED/ERROR node ids afterwards, not counts. The last known run was 12F / 1989P / 10E.

**Flush stale callers.** `PracticalAllocationInput` silently drops unknown kwargs (`extra="ignore"`), so a test that still passes `non_mf_equity_corpus` would keep passing while quietly testing something else. During implementation:
1. Set `extra="forbid"` temporarily.
2. Run the suite.
3. Fix every caller it flags.
4. Revert the setting.

Known affected tests:
- `test_preference_propagation_e2e.py:48`
- `test_rounding_at_final_step.py:44-50`
- `test_carveout_suspension.py`
- `test_debt_netting_keeps_held_debt.py:38`
- ainv `test_service.py:163-168,479-481`

**New tests (tracked)**
- Lump sum:
  - Goal with `amount_needed_fv` set explicitly to ₹6L, 12 months away; ₹50k profile SIP; ₹5L lump sum. Expect a goal share of ₹5L, and the PAA run receives `monthly_sip == 0.0`.
  - A gap smaller than the deploy: the goal share equals the gap, and the rest goes long-term.
- Threshold:
  - The three table rows, plus a new fund, a full exit and an `exit_flag` row, all through step 2.
  - Step 2b: a netted debt residual above 1% of the portfolio now trades, and one below the bar is still absorbed.
- Stocks:
  - `aggregate_holdings` drops equity-instrument rows from both `total_inr` and `by_subgroup`.
  - `pick_total_corpus` uses the stock-free holdings sum. One case: an AA stock row survives a later CAMS upload and must not lower the corpus.
  - Drift test: scan `*.py` runtime code under `AI_Agents/src` and `app/` for `non_mf_equity`, `excess_direct_stocks` and `SELL_DIRECT_STOCKS`. Exclude `tests/`, `Testing/`, `Master_testing/` and `alembic/`, and allowlist `practical_asset_allocation/models/run.py`, which keeps its columns.

**Rewrite (tracked tests that pin old behaviour)**
- Lump-sum share:
  - `test_lumpsum_uses_long_term_holdings_and_facts_count_goal_money`
  - `test_lumpsum_pins_corpus_and_passes_map`
- Stocks:
  - `test_facts_pack_direct_stock.py`
  - the stock cases in `test_rebalancing_practical_targets.py`, `test_human_override_step.py`, `test_holdings_snapshot.py` and `paa_engine/tests/test_input_builder.py`
  - `ainv_engine/tests/test_input_builder.py:74-94`, which expects the exclude set
  - `test_uniform_subgroup_pins.py:313-330`, `:712` and `:934-936`
  - `profile/tests/test_screen_full_distribution.py:426`
- Threshold: the `rebalance_min_change_pct` fixtures.

**Golden re-pin:** the `test_human_override_golden.py` fixture drops `non_mf_equity_corpus` and `financial_assets`, and `total_corpus` stays at ₹2Cr; the former ₹10L of stocks becomes ordinary corpus.
- The ideal golden must stay byte-identical. It never contained `financial_assets`.
- The practical golden is re-pinned. The only expected changes are:
  - the class totals are unchanged to within ₹1;
  - the `non_mf_equities` rows are gone;
  - the removed `CorpusBreakdown` keys are gone;
  - the ₹10L goes into MF equity subgroups and into a larger multi-asset sleeve, which is sized off the residual equity;
  - `arbitrage_plus_income` and `gold_commodities` shrink by the sleeve's debt and commodity slices;
  - the long-term bucket's `allocated_amount` rises by ₹10L.
- Three tracked tests in `test_uniform_subgroup_pins.py` pin this base, and are legitimately re-pinned: `NEUTRAL_SUBGROUP_MIX`, the variant tuples and the risk-9.5 sleeve. Their binding terms must not change.

**Gitignored folders** (`Testing/`, `Master_testing/`, `AI_Agents/lifecycle_sim_testing/`): update them locally wherever they break. Sweep with `/usr/bin/grep`; the shell `grep` skips gitignored files.
- The sim fails at runtime, not on import: `engines.py:77-161,236,257` reads `non_mf_equity_corpus`, and `run_ab.py:50` reads `REBALANCE_MIN_CHANGE_PCT`.
- The sim's `lumpsum()` also passes `monthly_sip=0.0`, so before/after comparisons stay honest.
- `Rebalancing/Testing/test_step2_dual_threshold.py` already tests an unbuilt 5%/10% design. Delete it.

**Docs**
- Keep these CLAUDE.md files current:
  - `AI_Agents/src/{practical_asset_allocation,Rebalancing,additional_investment}/`
  - `app/domains/{practical_asset_allocation,additional_investment,asset_allocation,portfolio}/`
  - The app PAA file's statement "only the SIP flow overrides monthly_sip" becomes false; the asset_allocation file gets the new `pick_total_corpus` rule.
- Reference docs are refreshed only when Amoul asks. When they are, they state the resulting logic in the present tense, with no change narrative ("no longer", "removed on…", "replaces the old…"). These become wrong:
  - Logics docs: `Additional_Investment.md`, `Practical_Asset_Allocation.md` and `Rebalancing.md`, which ground chat answers.
  - Rebalancing's own `Reference_docs/` specs.
  - The Module and Tech reference pages, and the business flowchart.
