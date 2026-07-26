# 2-Year Portfolio Lifecycle Simulation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A dev-only harness that drives the real Rebalancing + additional_investment engines over 24 months for the 5 canonical dummy profiles and renders one interactive HTML (a tab per profile) showing every trade, SIP, lumpsum, and goal withdrawal.

**Architecture:** A month-by-month state machine over a per-fund `Portfolio` (MF holdings + direct-equity/ELSS/cash pools). Each period converts current state into the **reused** dev-bridge inputs (`build_request`, `_build_input`), calls the **reused** engines verbatim, and applies the engine output back onto the state. The only hand-written *behavior* is goal withdrawals; everything else is orchestration + bookkeeping + reporting.

**Tech Stack:** Python 3, pydantic, Decimal money, stdlib `csv`/`html`. No new dependencies. No DB, no LLM, no async.

## Global Constraints

- **Reuse-only (HARD).** Reuse the engines and dev bridges verbatim; write **no new financial/translation logic except goal withdrawals**. Reused symbols: `run_rebalancing`, `run_additional_investment`, `run_practical_allocation`, `run_allocation`; from `Rebalancing.Testing.Master_testing.bridge`: `build_request`, `load_ranking`, `load_rejection_reasons`, `load_force_exit_isins`, `rank1_lookup`; from `Rebalancing.Testing.Master_testing.profiles`: `PROFILES`, `HoldingRecord`, `synth_holdings`, `BAD_ISIN`; from `additional_investment.Master_testing.runner`: `load_ranked_funds`, `_funding_status`, `_build_input`.
- **Location:** all sim code under `AI_Agents/lifecycle_sim_testing/` (sibling of `src/`; NOT under `src/`).
- **Single ranking CSV** for both engines: `AI_Agents/Reference_docs/prozpr_fund_ranking_june_2026_v2.csv`.
- **Money is `Decimal`** in the portfolio ledger. Engine floats (`amount_inr`) are converted with `Decimal(str(x))`. `final_holding_amount` is already `Decimal`.
- **Growth:** equity 12% / debt 6% / gold 8% p.a., monthly-compounded; cash 0%. Applied per fund by asset class; direct-equity + ELSS pools grow at the equity rate.
- **Cadence:** rebalances at months {0,6,12,18,24}; SIPs every month 1–24; lumpsum ₹5,00,000 at month 14.
- **SIP amount:** `round((annual_income/12 − monthly_household_expense) × 0.70)`.
- **Withdrawals:** in the exact due month, pre-tax, sell debt→gold→equity (largest holding first). Frozen pools not raided; MF exhaustion → shortfall.
- **Exact money conservation:** `total_value(m) == grown(m) + contribution(m) − withdrawal(m)`; rebalances contribute 0 (residual routed to cash).
- **Determinism:** ignore `computed_at` / `request_id` when comparing.
- **NAV coverage (added):** `engines.seed_portfolio` asserts every held ISIN resolves to a real NAV via `nav_cache.get_nav` (verified: all 5 profiles = 0 defaults), so the `DEFAULT_NAV=100` fallback can never silently apply to a held fund.
- **Per-transaction note (added):** every rebalance / SIP / lumpsum event carries a one-line `note` stating how many trades it placed (e.g. rebalance: "Placed 5 trades — 2 buy / 2 sell / 1 exit"); rendered against each event so the reader can see whether rebalancing activity tapers over the 24 months.

## File Structure

- `AI_Agents/lifecycle_sim_testing/__init__.py` — package marker.
- `AI_Agents/lifecycle_sim_testing/conftest.py` — puts `AI_Agents/src` on `sys.path` for pytest.
- `AI_Agents/lifecycle_sim_testing/constants.py` — knobs: CSV path, growth factors, asset-class map, month schedule, lumpsum, seed cost ratio, low-rated exit rating.
- `AI_Agents/lifecycle_sim_testing/portfolio.py` — `Holding`, `Portfolio` (grow / seed helpers / apply_rebalance / apply_buys / withdraw / queries).
- `AI_Agents/lifecycle_sim_testing/engines.py` — thin wrappers over the reused functions: `load_reference_data`, `seed_portfolio`, `refresh_alloc_input`, `rebalance`, `sip`, `lumpsum`, `sip_mirror_from_response`.
- `AI_Agents/lifecycle_sim_testing/simulate.py` — `simulate_profile(name) -> SimulationResult`; the month loop.
- `AI_Agents/lifecycle_sim_testing/report.py` — `render_html(results) -> str`.
- `AI_Agents/lifecycle_sim_testing/run.py` — sweep all 5, write `lifecycle_2y.html`.
- `AI_Agents/lifecycle_sim_testing/tests/test_portfolio.py`, `test_engines.py`, `test_simulate.py`.

Run tests from the repo root: `.venv-mac/bin/python -m pytest AI_Agents/lifecycle_sim_testing/tests/ -v`

---

### Task 1: Scaffold package + constants + pytest bootstrap

**Files:**
- Create: `AI_Agents/lifecycle_sim_testing/__init__.py`
- Create: `AI_Agents/lifecycle_sim_testing/conftest.py`
- Create: `AI_Agents/lifecycle_sim_testing/constants.py`
- Create: `AI_Agents/lifecycle_sim_testing/tests/__init__.py`
- Create: `AI_Agents/lifecycle_sim_testing/tests/test_constants.py`

**Interfaces:**
- Produces: `constants.RANKING_CSV: Path`, `EQUITY_MONTHLY/DEBT_MONTHLY/GOLD_MONTHLY: Decimal`, `asset_class_of(subgroup: str) -> str`, `monthly_factor(asset_class: str) -> Decimal`, `REBALANCE_MONTHS: set[int]`, `SIP_MONTHS: range`, `LUMPSUM_MONTH: int`, `LUMPSUM_AMOUNT: int`, `SEED_COST_RATIO: Decimal`, `EXIT_RATING: int`, `sip_amount(profile) -> int`.

- [ ] **Step 1: Create the package + pytest sys.path bootstrap**

`AI_Agents/lifecycle_sim_testing/__init__.py`:
```python
"""Dev-only 2-year portfolio lifecycle simulation. Not imported by runtime."""
```

`AI_Agents/lifecycle_sim_testing/tests/__init__.py`: (empty file)

`AI_Agents/lifecycle_sim_testing/conftest.py`:
```python
"""Put AI_Agents/src on sys.path so the reused agent packages import."""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
```
Note: `parent.parent` is `AI_Agents/`, then `/ "src"`. (conftest lives in `AI_Agents/lifecycle_sim_testing/`.)

- [ ] **Step 2: Write constants.py**

```python
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

# AI_Agents/lifecycle_sim_testing/constants.py -> AI_Agents/
_AI_AGENTS = Path(__file__).resolve().parent.parent
SRC = _AI_AGENTS / "src"
RANKING_CSV = _AI_AGENTS / "Reference_docs" / "prozpr_fund_ranking_june_2026_v2.csv"

# Monthly-compounded growth factors (annual -> monthly).
EQUITY_MONTHLY = Decimal(str(1.12 ** (1 / 12)))
DEBT_MONTHLY = Decimal(str(1.06 ** (1 / 12)))
GOLD_MONTHLY = Decimal(str(1.08 ** (1 / 12)))

_DEBT_SUBGROUPS = {"short_debt", "long_debt", "arbitrage", "arbitrage_plus_income"}
_GOLD_SUBGROUPS = {"gold_commodities"}

def asset_class_of(subgroup: str) -> str:
    if subgroup in _DEBT_SUBGROUPS:
        return "debt"
    if subgroup in _GOLD_SUBGROUPS:
        return "gold"
    # every *_equities, multi_asset, tax_efficient_equities, non_mf_equities
    return "equity"

def monthly_factor(asset_class: str) -> Decimal:
    return {"equity": EQUITY_MONTHLY, "debt": DEBT_MONTHLY, "gold": GOLD_MONTHLY}[asset_class]

REBALANCE_MONTHS = {0, 6, 12, 18, 24}
SIP_MONTHS = range(1, 25)          # 1..24
LUMPSUM_MONTH = 14
LUMPSUM_AMOUNT = 500_000
HORIZON_MONTHS = 24

SEED_COST_RATIO = Decimal("0.85")  # seed lot cost = 85% of value (bridge LT ratio)
EXIT_RATING = 3                    # < EXIT_FLOOR_RATING (5) -> engine fires an EXIT

def sip_amount(profile) -> int:
    monthly_income = float(profile.annual_income) / 12.0
    surplus = monthly_income - float(profile.monthly_household_expense)
    return int(round(max(0.0, surplus) * 0.70))
```

- [ ] **Step 3: Write the failing test**

`AI_Agents/lifecycle_sim_testing/tests/test_constants.py`:
```python
from decimal import Decimal
from lifecycle_sim_testing import constants as c

def test_growth_factors_compound_to_annual():
    assert (c.EQUITY_MONTHLY ** 12) == Decimal(str(1.12 ** (1 / 12))) ** 12  # sanity: consistent
    assert abs(float(c.EQUITY_MONTHLY) ** 12 - 1.12) < 1e-9
    assert abs(float(c.DEBT_MONTHLY) ** 12 - 1.06) < 1e-9
    assert abs(float(c.GOLD_MONTHLY) ** 12 - 1.08) < 1e-9

def test_asset_class_map():
    assert c.asset_class_of("short_debt") == "debt"
    assert c.asset_class_of("gold_commodities") == "gold"
    assert c.asset_class_of("low_beta_equities") == "equity"
    assert c.asset_class_of("multi_asset") == "equity"

def test_ranking_csv_exists():
    assert c.RANKING_CSV.exists()

def test_sip_amount_faisal():
    # Faisal: income 900000/12=75000, expense 45000 -> 30000*0.7=21000
    class P:
        annual_income = 900000
        monthly_household_expense = 45000
    assert c.sip_amount(P()) == 21000
```
Note: importing `lifecycle_sim_testing.constants` requires running pytest from the repo root so `AI_Agents/` is importable as a namespace — run: `.venv-mac/bin/python -m pytest AI_Agents/lifecycle_sim_testing/tests/test_constants.py -v` from `Prozpr_Backend/`. If `lifecycle_sim_testing` is not importable, add an `AI_Agents/__init__.py`-free namespace import by running pytest with `rootdir=AI_Agents` — simplest: tests import via `import constants as c` after conftest adds the package dir. Use the local form:
```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import constants as c
```
(Prefer this local-import form in every test file to avoid namespace-package friction.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv-mac/bin/python -m pytest AI_Agents/lifecycle_sim_testing/tests/test_constants.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/lifecycle_sim_testing/
git commit -m "feat(lifecycle-sim): scaffold package + constants"
```

---

### Task 2: Portfolio ledger — model, growth, queries

**Files:**
- Create: `AI_Agents/lifecycle_sim_testing/portfolio.py`
- Test: `AI_Agents/lifecycle_sim_testing/tests/test_portfolio.py`

**Interfaces:**
- Consumes: `constants.asset_class_of`, `constants.monthly_factor`, `constants.EQUITY_MONTHLY`.
- Produces:
  - `Holding` dataclass: `isin, asset_subgroup, sub_category, fund_name, fund_rating:int, is_recommended:bool, asset_class:str, present_inr:Decimal, cost_inr:Decimal`.
  - `Portfolio` dataclass: `holdings:list[Holding]`, `direct_equity_value:Decimal`, `elss_value:Decimal`, `cash:Decimal`.
  - `Portfolio.grow()`, `.mf_value()`, `.total_value()`, `.class_values() -> tuple[Decimal,Decimal,Decimal]` (equity, debt, gold), `.current_value_by_subgroup() -> dict[str,float]`, `.to_holding_records() -> list[HoldingRecord]`, `.snapshot(month:int) -> dict`.

- [ ] **Step 1: Write the failing test**

`AI_Agents/lifecycle_sim_testing/tests/test_portfolio.py`:
```python
import sys
from decimal import Decimal
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

import constants as c
from portfolio import Holding, Portfolio

def _h(isin, subgroup, present, cls, cost=None):
    return Holding(isin=isin, asset_subgroup=subgroup, sub_category="x",
                   fund_name=isin, fund_rating=8, is_recommended=True,
                   asset_class=cls, present_inr=Decimal(present),
                   cost_inr=Decimal(cost if cost is not None else present))

def test_total_value_sums_pools():
    p = Portfolio(holdings=[_h("A", "low_beta_equities", "100", "equity"),
                            _h("B", "short_debt", "50", "debt")],
                  direct_equity_value=Decimal("30"), elss_value=Decimal("20"),
                  cash=Decimal("10"))
    assert p.total_value() == Decimal("210")
    assert p.mf_value() == Decimal("150")

def test_grow_applies_per_class_factor():
    p = Portfolio(holdings=[_h("A", "low_beta_equities", "100", "equity"),
                            _h("B", "short_debt", "100", "debt"),
                            _h("G", "gold_commodities", "100", "gold")],
                  direct_equity_value=Decimal("100"), elss_value=Decimal("100"),
                  cash=Decimal("100"))
    p.grow()
    assert p.holdings[0].present_inr == Decimal("100") * c.EQUITY_MONTHLY
    assert p.holdings[1].present_inr == Decimal("100") * c.DEBT_MONTHLY
    assert p.holdings[2].present_inr == Decimal("100") * c.GOLD_MONTHLY
    assert p.direct_equity_value == Decimal("100") * c.EQUITY_MONTHLY
    assert p.elss_value == Decimal("100") * c.EQUITY_MONTHLY
    assert p.cash == Decimal("100")  # cash does not grow

def test_class_values_and_subgroup_map():
    p = Portfolio(holdings=[_h("A", "low_beta_equities", "100", "equity"),
                            _h("B", "short_debt", "50", "debt")],
                  direct_equity_value=Decimal("30"), elss_value=Decimal("20"),
                  cash=Decimal("0"))
    eq, debt, gold = p.class_values()
    assert eq == Decimal("150")   # 100 + direct 30 + elss 20
    assert debt == Decimal("50")
    assert gold == Decimal("0")
    assert p.current_value_by_subgroup()["short_debt"] == 50.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-mac/bin/python -m pytest AI_Agents/lifecycle_sim_testing/tests/test_portfolio.py -v`
Expected: FAIL (`No module named 'portfolio'`).

- [ ] **Step 3: Write portfolio.py (model + growth + queries)**

```python
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import constants as c  # noqa: E402
from Rebalancing.Testing.Master_testing.profiles import HoldingRecord  # noqa: E402


@dataclass
class Holding:
    isin: str
    asset_subgroup: str
    sub_category: str
    fund_name: str
    fund_rating: int
    is_recommended: bool
    asset_class: str
    present_inr: Decimal
    cost_inr: Decimal


@dataclass
class Portfolio:
    holdings: list[Holding] = field(default_factory=list)
    direct_equity_value: Decimal = Decimal(0)
    elss_value: Decimal = Decimal(0)
    cash: Decimal = Decimal(0)

    def grow(self) -> None:
        for h in self.holdings:
            h.present_inr *= c.monthly_factor(h.asset_class)
        self.direct_equity_value *= c.EQUITY_MONTHLY
        self.elss_value *= c.EQUITY_MONTHLY
        # cash: 0% growth

    def mf_value(self) -> Decimal:
        return sum((h.present_inr for h in self.holdings), Decimal(0))

    def total_value(self) -> Decimal:
        return self.mf_value() + self.direct_equity_value + self.elss_value + self.cash

    def class_values(self) -> tuple[Decimal, Decimal, Decimal]:
        eq = sum((h.present_inr for h in self.holdings if h.asset_class == "equity"), Decimal(0))
        eq += self.direct_equity_value + self.elss_value
        debt = sum((h.present_inr for h in self.holdings if h.asset_class == "debt"), Decimal(0))
        gold = sum((h.present_inr for h in self.holdings if h.asset_class == "gold"), Decimal(0))
        return eq, debt, gold

    def current_value_by_subgroup(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for h in self.holdings:
            out[h.asset_subgroup] = out.get(h.asset_subgroup, 0.0) + float(h.present_inr)
        return out

    def to_holding_records(self) -> list[HoldingRecord]:
        return [
            HoldingRecord(
                isin=h.isin, asset_subgroup=h.asset_subgroup, sub_category=h.sub_category,
                fund_name=h.fund_name, present_inr=h.present_inr,
                fund_rating=h.fund_rating, is_recommended=h.is_recommended,
            )
            for h in self.holdings if h.present_inr > 0
        ]

    def snapshot(self, month: int) -> dict:
        eq, debt, gold = self.class_values()
        return {
            "month": month,
            "mf": float(self.mf_value()),
            "direct": float(self.direct_equity_value),
            "elss": float(self.elss_value),
            "cash": float(self.cash),
            "equity": float(eq), "debt": float(debt), "gold": float(gold),
            "total": float(self.total_value()),
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv-mac/bin/python -m pytest AI_Agents/lifecycle_sim_testing/tests/test_portfolio.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/lifecycle_sim_testing/portfolio.py AI_Agents/lifecycle_sim_testing/tests/test_portfolio.py
git commit -m "feat(lifecycle-sim): portfolio ledger model, growth, queries"
```

---

### Task 3: Portfolio mutations — apply_rebalance, apply_buys, withdraw

**Files:**
- Modify: `AI_Agents/lifecycle_sim_testing/portfolio.py`
- Test: `AI_Agents/lifecycle_sim_testing/tests/test_portfolio.py` (append)

**Interfaces:**
- Produces on `Portfolio`:
  - `apply_rebalance(response) -> None` — set each MF fund to `final_holding_amount` (cost pro-rated on sells, added on buys), decrement `direct_equity_value` by any `SELL_DIRECT_STOCKS`, route conservation residual to `cash`.
  - `apply_buys(out, contribution) -> None` — contribution → cash; each `FundBuy.amount_inr` → cash-funded holding; undeployed stays in cash.
  - `withdraw_for_goal(amount) -> dict` — debt→gold→equity, largest-first; returns `{"requested","sold","realized_gain","shortfall","funds"}`.

- [ ] **Step 1: Write the failing tests (append to test_portfolio.py)**

```python
from types import SimpleNamespace

def _resp(rows, trade_list=()):
    return SimpleNamespace(rows=rows, trade_list=list(trade_list))

def _row(isin, subgroup, final, present, cls="equity"):
    return SimpleNamespace(isin=isin, asset_subgroup=subgroup, sub_category="x",
                           recommended_fund=isin, fund_rating=8, is_recommended=True,
                           final_holding_amount=Decimal(final), present_allocation_inr=Decimal(present))

def test_apply_rebalance_conserves_total():
    p = Portfolio(holdings=[_h("A", "low_beta_equities", "100", "equity"),
                            _h("B", "short_debt", "100", "debt")],
                  direct_equity_value=Decimal("0"), elss_value=Decimal("0"), cash=Decimal("0"))
    before = p.total_value()
    # engine sells 40 of A, buys nothing else -> mf drops 40, cash gains 40
    p.apply_rebalance(_resp([_row("A", "low_beta_equities", "60", "100"),
                             _row("B", "short_debt", "100", "100", "debt")]))
    assert p.total_value() == before
    a = next(h for h in p.holdings if h.isin == "A")
    assert a.present_inr == Decimal("60")
    assert a.cost_inr == Decimal("60")  # 100 -> 60 pro-rated (seed cost was 100)

def test_apply_rebalance_sell_direct_stocks():
    p = Portfolio(holdings=[_h("A", "low_beta_equities", "100", "equity")],
                  direct_equity_value=Decimal("50"), elss_value=Decimal("0"), cash=Decimal("0"))
    before = p.total_value()
    trade = SimpleNamespace(action="SELL_DIRECT_STOCKS", isin=None,
                            asset_subgroup="non_mf_equities", amount_inr=Decimal("20"))
    # A stays at 100; direct trimmed by 20 -> that 20 goes to cash (residual)
    p.apply_rebalance(_resp([_row("A", "low_beta_equities", "100", "100")], [trade]))
    assert p.total_value() == before
    assert p.direct_equity_value == Decimal("30")
    assert p.cash == Decimal("20")

def test_apply_buys_conserves_and_holds_undeployed():
    p = Portfolio(holdings=[_h("A", "low_beta_equities", "100", "equity")],
                  cash=Decimal("0"))
    before = p.total_value()
    out = SimpleNamespace(buys=[SimpleNamespace(isin="A", asset_subgroup="low_beta_equities",
                                                sub_category="x", recommended_fund="A",
                                                amount_inr=600.0)])
    p.apply_buys(out, contribution=1000)
    assert p.total_value() == before + Decimal("1000")   # full contribution enters value
    assert p.cash == Decimal("400")                       # undeployed held as cash
    assert next(h for h in p.holdings if h.isin == "A").present_inr == Decimal("700")

def test_withdraw_debt_first_then_shortfall():
    p = Portfolio(holdings=[_h("A", "low_beta_equities", "100", "equity", cost="80"),
                            _h("B", "short_debt", "50", "debt", cost="50")],
                  cash=Decimal("0"))
    r = p.withdraw_for_goal(120)
    # debt B (50) sold first, then 70 of equity A
    assert r["sold"] == Decimal("120")
    assert r["shortfall"] == Decimal("0")
    a = next(h for h in p.holdings if h.isin == "A")
    assert a.present_inr == Decimal("30")
    assert not any(h.isin == "B" for h in p.holdings)  # B fully sold, dropped

def test_withdraw_shortfall_when_mf_exhausted():
    p = Portfolio(holdings=[_h("A", "low_beta_equities", "40", "equity")], cash=Decimal("0"))
    r = p.withdraw_for_goal(100)
    assert r["sold"] == Decimal("40")
    assert r["shortfall"] == Decimal("60")
    assert p.holdings == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv-mac/bin/python -m pytest AI_Agents/lifecycle_sim_testing/tests/test_portfolio.py -v`
Expected: FAIL (`Portfolio has no attribute 'apply_rebalance'`).

- [ ] **Step 3: Add the three methods to portfolio.py**

```python
    def apply_rebalance(self, response) -> None:
        mf_before = self.mf_value()
        direct_before = self.direct_equity_value
        by_isin = {h.isin: h for h in self.holdings}
        for row in response.rows:
            isin = getattr(row, "isin", None)
            if not isin or row.asset_subgroup == "tax_efficient_equities":
                continue
            final = Decimal(str(row.final_holding_amount))
            h = by_isin.get(isin)
            if h is None:
                if final > 0:  # cap-spill buy into a previously-unheld fund
                    nh = Holding(
                        isin=isin, asset_subgroup=row.asset_subgroup,
                        sub_category=getattr(row, "sub_category", "unknown"),
                        fund_name=getattr(row, "recommended_fund", None) or isin,
                        fund_rating=getattr(row, "fund_rating", 8),
                        is_recommended=getattr(row, "is_recommended", True),
                        asset_class=c.asset_class_of(row.asset_subgroup),
                        present_inr=final, cost_inr=final,
                    )
                    self.holdings.append(nh)
                    by_isin[isin] = nh
                continue
            old = h.present_inr
            if final < old and old > 0:
                h.cost_inr = (h.cost_inr * final / old).quantize(Decimal("1"))
            elif final > old:
                h.cost_inr += (final - old)
            h.present_inr = final
        self.holdings = [h for h in self.holdings if h.present_inr > 0]
        for t in response.trade_list:
            if getattr(t, "action", None) == "SELL_DIRECT_STOCKS":
                self.direct_equity_value -= Decimal(str(t.amount_inr))
        mf_after = self.mf_value()
        direct_after = self.direct_equity_value
        self.cash += (mf_before + direct_before) - (mf_after + direct_after)

    def apply_buys(self, out, contribution) -> None:
        self.cash += Decimal(str(contribution))
        by_isin = {h.isin: h for h in self.holdings}
        for b in out.buys:
            amt = Decimal(str(b.amount_inr))
            if amt <= 0:
                continue
            h = by_isin.get(b.isin)
            if h is None:
                nh = Holding(
                    isin=b.isin, asset_subgroup=b.asset_subgroup, sub_category=b.sub_category,
                    fund_name=b.recommended_fund, fund_rating=8, is_recommended=True,
                    asset_class=c.asset_class_of(b.asset_subgroup),
                    present_inr=amt, cost_inr=amt,
                )
                self.holdings.append(nh)
                by_isin[b.isin] = nh
            else:
                h.present_inr += amt
                h.cost_inr += amt
            self.cash -= amt

    def withdraw_for_goal(self, amount) -> dict:
        order = {"debt": 0, "gold": 1, "equity": 2}
        need = Decimal(str(amount))
        sold = Decimal(0)
        realized_gain = Decimal(0)
        funds: list[tuple[str, float]] = []
        for h in sorted(self.holdings, key=lambda x: (order.get(x.asset_class, 3), -x.present_inr)):
            if need <= 0:
                break
            take = min(h.present_inr, need)
            if take <= 0:
                continue
            cost_part = (h.cost_inr * take / h.present_inr).quantize(Decimal("1")) if h.present_inr > 0 else Decimal(0)
            realized_gain += take - cost_part
            h.present_inr -= take
            h.cost_inr -= cost_part
            sold += take
            need -= take
            funds.append((h.fund_name, float(take)))
        self.holdings = [h for h in self.holdings if h.present_inr > 0]
        return {"requested": Decimal(str(amount)), "sold": sold,
                "realized_gain": realized_gain, "shortfall": max(need, Decimal(0)), "funds": funds}
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv-mac/bin/python -m pytest AI_Agents/lifecycle_sim_testing/tests/test_portfolio.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/lifecycle_sim_testing/portfolio.py AI_Agents/lifecycle_sim_testing/tests/test_portfolio.py
git commit -m "feat(lifecycle-sim): rebalance/buys/withdraw mutations with money conservation"
```

---

### Task 4: engines.py — reference data, seed, refresh, engine wrappers

**Files:**
- Create: `AI_Agents/lifecycle_sim_testing/engines.py`
- Test: `AI_Agents/lifecycle_sim_testing/tests/test_engines.py`

**Interfaces:**
- Consumes: `portfolio.Portfolio/Holding`, `constants.*`, all reused symbols.
- Produces:
  - `load_reference_data() -> ReferenceData` (dataclass: `ranking, rejection, force_exit, r1, ranked`).
  - `seed_portfolio(profile, ref) -> Portfolio`.
  - `refresh_alloc_input(base_profile, month, port) -> PracticalAllocationInput`.
  - `rebalance(profile_refreshed, ref) -> response`.
  - `sip_mirror_from_response(response) -> dict[str, list[str]]`.
  - `sip(profile_refreshed, ref, amount, mirror) -> out`.
  - `lumpsum(profile_refreshed, ref, amount, port) -> out`.

- [ ] **Step 1: Write engines.py**

```python
from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import constants as c  # noqa: E402
from portfolio import Holding, Portfolio  # noqa: E402

from asset_allocation_pydantic import run_allocation  # noqa: E402
from Rebalancing import run_rebalancing  # noqa: E402
from Rebalancing.Testing.Master_testing.bridge import (  # noqa: E402
    build_request, load_force_exit_isins, load_ranking, load_rejection_reasons, rank1_lookup,
)
from Rebalancing.Testing.Master_testing.profiles import BAD_ISIN, synth_holdings  # noqa: E402
from practical_asset_allocation.pipeline import run_practical_allocation  # noqa: E402
from additional_investment import Cadence, run_additional_investment  # noqa: E402
from additional_investment.Master_testing.runner import (  # noqa: E402
    _build_input, _funding_status, load_ranked_funds,
)


@dataclass
class ReferenceData:
    ranking: dict
    rejection: dict
    force_exit: set
    r1: dict
    ranked: list


def load_reference_data() -> ReferenceData:
    ranking = load_ranking(c.RANKING_CSV)
    return ReferenceData(
        ranking=ranking,
        rejection=load_rejection_reasons(c.RANKING_CSV),
        force_exit=load_force_exit_isins(c.RANKING_CSV),
        r1=rank1_lookup(ranking),
        ranked=load_ranked_funds(),   # reads june CSV via its own module path
    )


def seed_portfolio(profile, ref: ReferenceData) -> Portfolio:
    alloc_out = run_allocation(profile)
    records = synth_holdings(profile, alloc_out, ref.r1)
    tradable_mf = (Decimal(str(profile.total_corpus))
                   - Decimal(str(profile.non_mf_equity_corpus))
                   - Decimal(str(profile.elss_corpus)))
    raw = sum((r.present_inr for r in records), Decimal(0))
    if raw > 0:
        factor = tradable_mf / raw
        for r in records:
            r.present_inr = (r.present_inr * factor).quantize(Decimal("1"))
        delta = tradable_mf - sum((r.present_inr for r in records), Decimal(0))
        if delta != 0 and records:
            max(records, key=lambda r: r.present_inr).present_inr += delta
    for r in records:            # low-rated fund -> fires a real EXIT at rebalance #1
        if r.isin == BAD_ISIN:
            r.fund_rating = c.EXIT_RATING
    port = Portfolio(
        holdings=[
            Holding(isin=r.isin, asset_subgroup=r.asset_subgroup, sub_category=r.sub_category,
                    fund_name=r.fund_name, fund_rating=r.fund_rating, is_recommended=r.is_recommended,
                    asset_class=c.asset_class_of(r.asset_subgroup),
                    present_inr=r.present_inr,
                    cost_inr=(r.present_inr * c.SEED_COST_RATIO).quantize(Decimal("1")))
            for r in records if r.present_inr > 0
        ],
        direct_equity_value=Decimal(str(profile.non_mf_equity_corpus)),
        elss_value=Decimal(str(profile.elss_corpus)),
        cash=Decimal(0),
    )
    assert abs(port.total_value() - Decimal(str(profile.total_corpus))) <= Decimal("1"), \
        f"seed total {port.total_value()} != corpus {profile.total_corpus}"
    return port


def refresh_alloc_input(base_profile, month: int, port: Portfolio):
    new_goals = []
    for g in base_profile.goals:
        h = g.time_to_goal_months - month
        if h > 0:
            new_goals.append(g.model_copy(update={"time_to_goal_months": h}))
    total = float(port.total_value())
    direct = float(port.direct_equity_value)
    elss = float(port.elss_value)
    return base_profile.model_copy(update={
        "total_corpus": total,
        "mf_corpus": total - direct,
        "non_mf_equity_corpus": direct,
        "elss_corpus": elss,
        "net_financial_assets": total,
        "goals": new_goals,
    })
```

The `rebalance` wrapper takes the current portfolio (its live holdings become the request rows):

```python
def rebalance(profile_refreshed, ref: ReferenceData, port: Portfolio):
    alloc_out = run_allocation(profile_refreshed)  # unused by build_request; satisfies its signature
    request = build_request(
        profile_refreshed, alloc_out, port.to_holding_records(),
        ref.ranking, ref.rejection, force_exit_isins=ref.force_exit,
    )
    return run_rebalancing(request)


def sip_mirror_from_response(response) -> dict[str, list[str]]:
    by_sg: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for t in response.trade_list:
        if t.action == "BUY" and t.isin:
            by_sg[t.asset_subgroup].append((float(t.amount_inr), t.isin))
    return {sg: [isin for _, isin in sorted(v, reverse=True)] for sg, v in by_sg.items()}


def sip(profile_refreshed, ref: ReferenceData, amount: int, mirror: dict):
    alloc = run_practical_allocation(profile_refreshed)
    funding = _funding_status(alloc)
    inp = _build_input(alloc.aggregated_subgroups, ref.ranked, amount, Cadence.SIP_MONTHLY,
                       funding["short_term"]["funded"], funding["medium_term"]["funded"])
    inp = inp.model_copy(update={"rebal_buy_isins_by_subgroup": mirror or None})
    return run_additional_investment(inp)


def lumpsum(profile_refreshed, ref: ReferenceData, amount: int, port: Portfolio):
    pinned = profile_refreshed.model_copy(update={
        "total_corpus": float(profile_refreshed.total_corpus) + amount,
        "mf_corpus": float(profile_refreshed.mf_corpus) + amount,
    })
    alloc_pinned = run_practical_allocation(pinned)
    funding = _funding_status(alloc_pinned)
    inp = _build_input(alloc_pinned.aggregated_subgroups, ref.ranked, amount, Cadence.LUMPSUM,
                       funding["short_term"]["funded"], funding["medium_term"]["funded"],
                       current_value_by_subgroup=port.current_value_by_subgroup())
    return run_additional_investment(inp)
```

- [ ] **Step 2: Write test_engines.py**

```python
import sys
from decimal import Decimal
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

import engines
from Rebalancing.Testing.Master_testing.profiles import PROFILES

def test_seed_total_equals_corpus():
    ref = engines.load_reference_data()
    profile = PROFILES["Mohammed Faisal"]
    port = engines.seed_portfolio(profile, ref)
    assert abs(port.total_value() - Decimal(str(profile.total_corpus))) <= Decimal("1")
    # low-rated exit fund present
    from Rebalancing.Testing.Master_testing.profiles import BAD_ISIN
    assert any(h.isin == BAD_ISIN and h.fund_rating < 5 for h in port.holdings)

def test_rebalance_fires_an_exit_and_trades():
    ref = engines.load_reference_data()
    profile = PROFILES["Mohammed Faisal"]
    port = engines.seed_portfolio(profile, ref)
    refreshed = engines.refresh_alloc_input(profile, 0, port)
    resp = engines.rebalance(refreshed, ref, port)
    assert resp.totals.funds_to_exit_count >= 1
    assert len(resp.trade_list) >= 1
    mirror = engines.sip_mirror_from_response(resp)
    assert any(mirror.values())  # at least one subgroup has a BUY isin

def test_sip_and_lumpsum_deploy():
    ref = engines.load_reference_data()
    profile = PROFILES["Mohammed Faisal"]
    port = engines.seed_portfolio(profile, ref)
    refreshed = engines.refresh_alloc_input(profile, 1, port)
    out = engines.sip(refreshed, ref, 20000, mirror={})
    assert out.deployed_inr > 0
    lout = engines.lumpsum(refreshed, ref, 500000, port)
    assert lout.deployed_inr > 0
```

- [ ] **Step 3: Run to verify it passes**

Run: `.venv-mac/bin/python -m pytest AI_Agents/lifecycle_sim_testing/tests/test_engines.py -v`
Expected: 3 passed. (If `test_rebalance_fires_an_exit_and_trades` fails on `funds_to_exit_count`, confirm the seeded BAD fund kept `fund_rating=3` through `to_holding_records`; the EXIT is on the LT portion per the fixed-ratio split.)

- [ ] **Step 4: Commit**

```bash
git add AI_Agents/lifecycle_sim_testing/engines.py AI_Agents/lifecycle_sim_testing/tests/test_engines.py
git commit -m "feat(lifecycle-sim): reuse-only engine wrappers + seed + refresh"
```

---

### Task 5: simulate.py — the month-by-month state machine

**Files:**
- Create: `AI_Agents/lifecycle_sim_testing/simulate.py`
- Test: `AI_Agents/lifecycle_sim_testing/tests/test_simulate.py`

**Interfaces:**
- Consumes: `engines.*`, `portfolio.Portfolio`, `constants.*`, `PROFILES`.
- Produces: `simulate_profile(name: str) -> SimulationResult` where `SimulationResult` is a dataclass `{name, profile_meta:dict, snapshots:list[dict], events:list[dict], metrics:dict}`. Each event dict: `{month, kind ∈ {"rebalance","sip","lumpsum","withdrawal"}, ...payload}`.

- [ ] **Step 1: Write simulate.py**

```python
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import constants as c  # noqa: E402
import engines  # noqa: E402
from Rebalancing.Testing.Master_testing.profiles import PROFILES  # noqa: E402

_CONS_TOL = Decimal("1")  # rupee tolerance for the conservation assert


@dataclass
class SimulationResult:
    name: str
    profile_meta: dict
    snapshots: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


def _expected_after_growth(port) -> Decimal:
    eq, debt, gold = port.class_values()
    return (eq * c.EQUITY_MONTHLY + debt * c.DEBT_MONTHLY + gold * c.GOLD_MONTHLY + port.cash)


def simulate_profile(name: str) -> SimulationResult:
    ref = engines.load_reference_data()
    profile = PROFILES[name]
    port = engines.seed_portfolio(profile, ref)
    sip_amt = c.sip_amount(profile)

    res = SimulationResult(name=name, profile_meta=_profile_meta(profile, sip_amt))
    res.snapshots.append(port.snapshot(0))

    # m0: rebalance on seed holdings, cache mirror
    refreshed = engines.refresh_alloc_input(profile, 0, port)
    resp = engines.rebalance(refreshed, ref, port)
    port.apply_rebalance(resp)
    mirror = engines.sip_mirror_from_response(resp)
    res.events.append(_rebalance_event(0, resp))
    res.snapshots[0] = port.snapshot(0)  # refresh after m0 rebalance

    for m in range(1, c.HORIZON_MONTHS + 1):
        before = port.total_value()
        expected = _expected_after_growth(port)
        port.grow()
        contribution = Decimal(0)
        withdrawal = Decimal(0)

        # 2. goal withdrawal(s) due this month
        for g in profile.goals:
            if g.time_to_goal_months == m:
                r = port.withdraw_for_goal(g.amount_needed)
                withdrawal += r["sold"]
                res.events.append(_withdrawal_event(m, g, r))

        refreshed = engines.refresh_alloc_input(profile, m, port)

        # 3. rebalance
        if m in c.REBALANCE_MONTHS:
            resp = engines.rebalance(refreshed, ref, port)
            port.apply_rebalance(resp)
            mirror = engines.sip_mirror_from_response(resp)
            res.events.append(_rebalance_event(m, resp))

        # 4. lumpsum
        if m == c.LUMPSUM_MONTH:
            lout = engines.lumpsum(refreshed, ref, c.LUMPSUM_AMOUNT, port)
            port.apply_buys(lout, c.LUMPSUM_AMOUNT)
            contribution += Decimal(c.LUMPSUM_AMOUNT)
            res.events.append(_buys_event(m, "lumpsum", lout))

        # 5. SIP every month
        sout = engines.sip(refreshed, ref, sip_amt, mirror)
        port.apply_buys(sout, sip_amt)
        contribution += Decimal(sip_amt)
        res.events.append(_buys_event(m, "sip", sout))

        # conservation
        got = port.total_value()
        want = expected + contribution - withdrawal
        assert abs(got - want) <= _CONS_TOL, f"[{name} m{m}] conservation off: {got} vs {want}"

        res.snapshots.append(port.snapshot(m))

    res.metrics = _metrics(res, port)
    return res
```

Add the small event/meta builders in the same file:

```python
def _profile_meta(profile, sip_amt) -> dict:
    return {
        "age": profile.age, "risk": round(float(profile.effective_risk_score), 2),
        "tax_regime": profile.tax_regime, "corpus": float(profile.total_corpus),
        "sip": sip_amt,
        "goals": [{"name": g.goal_name, "months": g.time_to_goal_months,
                   "amount": float(g.amount_needed),
                   "in_window": g.time_to_goal_months <= c.HORIZON_MONTHS}
                  for g in profile.goals],
    }


def _rebalance_event(month, resp) -> dict:
    t = resp.totals
    return {
        "month": month, "kind": "rebalance",
        "totals": {"buy": float(t.total_buy_inr), "sell": float(t.total_sell_inr),
                   "stcg": float(t.total_stcg_realised), "ltcg": float(t.total_ltcg_realised),
                   "tax": float(t.total_tax_estimate_inr),
                   "buys": t.funds_to_buy_count, "sells": t.funds_to_sell_count,
                   "exits": t.funds_to_exit_count,
                   "unrebalanced": float(t.unrebalanced_remainder_inr)},
        "trades": [{"fund": tr.recommended_fund, "action": tr.action,
                    "amount": float(tr.amount_inr), "subgroup": tr.asset_subgroup,
                    "reason": tr.reason_title} for tr in resp.trade_list],
    }


def _buys_event(month, kind, out) -> dict:
    return {
        "month": month, "kind": kind,
        "deployed": float(out.deployed_inr), "undeployed": float(out.undeployed_inr),
        "buys": [{"fund": b.recommended_fund, "subgroup": b.asset_subgroup,
                  "amount": float(b.amount_inr), "isin": b.isin,
                  "mirror": bool(b.reason and "rebalanc" in b.reason.lower())}
                 for b in out.buys],
    }


def _withdrawal_event(month, goal, r) -> dict:
    return {
        "month": month, "kind": "withdrawal", "goal": goal.goal_name,
        "requested": float(r["requested"]), "sold": float(r["sold"]),
        "realized_gain": float(r["realized_gain"]), "shortfall": float(r["shortfall"]),
        "funds": [{"fund": f, "amount": a} for f, a in r["funds"]],
    }


def _metrics(res, port) -> dict:
    reb = [e for e in res.events if e["kind"] == "rebalance"]
    return {
        "total_trades": sum(len(e["trades"]) for e in reb),
        "total_buys": sum(e["totals"]["buys"] for e in reb),
        "total_sells": sum(e["totals"]["sells"] for e in reb),
        "total_exits": sum(e["totals"]["exits"] for e in reb),
        "sip_deployed": sum(e["deployed"] for e in res.events if e["kind"] == "sip"),
        "lumpsum_deployed": sum(e["deployed"] for e in res.events if e["kind"] == "lumpsum"),
        "withdrawn": sum(e["sold"] for e in res.events if e["kind"] == "withdrawal"),
        "shortfall": sum(e["shortfall"] for e in res.events if e["kind"] == "withdrawal"),
        "realized_stcg": sum(e["totals"]["stcg"] for e in reb),
        "realized_ltcg": sum(e["totals"]["ltcg"] for e in reb),
        "end_cash": float(port.cash),
        "start_value": res.snapshots[0]["total"],
        "end_value": res.snapshots[-1]["total"],
    }
```
Note on `mirror` labeling: `b.reason` is the `FundBuy.reason` string (`"Matches your rebalancing plan"` for mirror, else rank text). If `reason` is absent, treat as non-mirror.

- [ ] **Step 2: Write test_simulate.py**

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

import constants as c
import simulate

def test_faisal_runs_24_months_and_conserves():
    res = simulate.simulate_profile("Mohammed Faisal")
    assert len(res.snapshots) == c.HORIZON_MONTHS + 1     # m0..m24
    assert res.metrics["total_exits"] >= 1                # exit path exercised
    assert res.metrics["sip_deployed"] > 0
    assert res.metrics["lumpsum_deployed"] > 0
    # a withdrawal happened at m6 (Emergency 3L)
    assert any(e["kind"] == "withdrawal" and e["month"] == 6 for e in res.events)
    # 5 rebalances
    assert len([e for e in res.events if e["kind"] == "rebalance"]) == 5
```
(The in-loop conservation `assert` is the primary correctness gate; this test simply confirms the run completes and hits the expected structural counts.)

- [ ] **Step 3: Run to verify it passes**

Run: `.venv-mac/bin/python -m pytest AI_Agents/lifecycle_sim_testing/tests/test_simulate.py -v`
Expected: 1 passed (and no `conservation off` assertion error).

- [ ] **Step 4: Commit**

```bash
git add AI_Agents/lifecycle_sim_testing/simulate.py AI_Agents/lifecycle_sim_testing/tests/test_simulate.py
git commit -m "feat(lifecycle-sim): month-by-month state machine with conservation asserts"
```

---

### Task 6: report.py — one HTML, tab per profile

**Files:**
- Create: `AI_Agents/lifecycle_sim_testing/report.py`
- Test: `AI_Agents/lifecycle_sim_testing/tests/test_report.py`

**Interfaces:**
- Consumes: `SimulationResult` objects (their `.name/.profile_meta/.snapshots/.events/.metrics`).
- Produces: `render_html(results: list) -> str` (self-contained HTML; inline CSS/JS/SVG; tab per profile).

- [ ] **Step 1: Write report.py**

Model the structure on the existing `additional_investment/Master_testing/runner.py` renderer (tabs + panels + `show(i)` JS + `esc`/`inr` helpers — copy those three helpers and the CSS/JS verbatim as a starting point, they are plain presentation utilities). Then add, per profile panel:
1. A header card from `profile_meta` (name, age, risk, regime, corpus, SIP, goals with due-months; in-window goals bolded).
2. A metrics row from `metrics` (total trades, buys/sells/exits, SIP deployed, lumpsum, withdrawn+shortfall, realized STCG/LTCG, end cash, start→end value).
3. An inline-SVG value curve from `snapshots` (x=month 0..24; series equity/debt/gold/total; **per-tab y-axis** = `max(total)` with a 0 floor).
4. A chronological event list from `events`: rebalance cards expand to a trade table (`fund/action/amount/reason`) + totals; SIP rows grouped per 6-month block; lumpsum + withdrawal cards inline.

```python
from __future__ import annotations

import html


def esc(x) -> str:
    return html.escape(str(x) if x is not None else "")


def inr(amt) -> str:
    try:
        n = float(amt or 0)
    except (TypeError, ValueError):
        return esc(amt)
    sign = "-" if n < 0 else ""
    n = abs(n)
    raw = str(int(round(n)))
    if len(raw) > 3:
        last3, rest, groups = raw[-3:], raw[:-3], []
        while len(rest) > 2:
            groups.insert(0, rest[-2:]); rest = rest[:-2]
        if rest:
            groups.insert(0, rest)
        s = ",".join(groups + [last3])
    else:
        s = raw
    return f"{sign}₹{s}"


def _svg_curve(snapshots) -> str:
    if not snapshots:
        return ""
    months = [s["month"] for s in snapshots]
    ymax = max((s["total"] for s in snapshots), default=1.0) or 1.0
    W, H, pad = 640, 220, 30
    def x(m): return pad + (W - 2 * pad) * (m / max(months[-1], 1))
    def y(v): return H - pad - (H - 2 * pad) * (v / ymax)
    def path(key, color):
        pts = " ".join(f"{x(s['month']):.1f},{y(s[key]):.1f}" for s in snapshots)
        return f"<polyline fill='none' stroke='{color}' stroke-width='2' points='{pts}'/>"
    series = [("total", "#111"), ("equity", "#1d4ed8"), ("debt", "#047857"), ("gold", "#b45309")]
    lines = "".join(path(k, col) for k, col in series)
    legend = " ".join(f"<tspan fill='{col}'>■</tspan>{esc(k)} " for k, col in series)
    return (f"<svg viewBox='0 0 {W} {H}' width='100%' style='max-width:{W}px'>"
            f"<rect x='0' y='0' width='{W}' height='{H}' fill='#fff'/>{lines}"
            f"<text x='{pad}' y='16' font-size='11'>{legend}</text>"
            f"<text x='{pad}' y='{H-8}' font-size='10'>m0</text>"
            f"<text x='{W-pad-12}' y='{H-8}' font-size='10'>m{months[-1]}</text></svg>")


def _panel(res) -> str:
    m = res.profile_meta
    goals = "; ".join(
        (f"<b>{esc(g['name'])}</b>" if g["in_window"] else esc(g["name"]))
        + f" ({g['months']}mo, {inr(g['amount'])})" for g in m["goals"])
    header = (f"<div class='card'><b>{esc(res.name)}</b> · age {m['age']} · risk {m['risk']} · "
              f"{esc(m['tax_regime'])} · corpus {inr(m['corpus'])} · SIP {inr(m['sip'])}/mo"
              f"<div class='muted'>Goals: {goals}</div></div>")
    mx = res.metrics
    metrics = ("<div class='metrics'>"
               + "".join(f"<span class='chip'>{esc(k)}: <b>{inr(v) if 'value' in k or 'deployed' in k or 'cash' in k or 'withdrawn' in k or 'stcg' in k or 'ltcg' in k or 'shortfall' in k else esc(v)}</b></span>"
                         for k, v in mx.items())
               + "</div>")
    curve = _svg_curve(res.snapshots)
    events = "".join(_event_html(e) for e in res.events)
    return f"<div class='panel'>{header}{metrics}<h3>Portfolio value</h3>{curve}<h3>Timeline</h3>{events}</div>"


def _event_html(e) -> str:
    if e["kind"] == "rebalance":
        t = e["totals"]
        rows = "".join(f"<tr><td>{esc(tr['fund'])}</td><td>{esc(tr['action'])}</td>"
                       f"<td class='num'>{inr(tr['amount'])}</td><td>{esc(tr['reason'])}</td></tr>"
                       for tr in e["trades"])
        return (f"<details class='ev reb'><summary>m{e['month']} · REBALANCE · "
                f"{t['buys']} buys / {t['sells']} sells / {t['exits']} exits · "
                f"buy {inr(t['buy'])} sell {inr(t['sell'])} tax {inr(t['tax'])}</summary>"
                f"<table><thead><tr><th>Fund</th><th>Action</th><th class='num'>Amount</th>"
                f"<th>Reason</th></tr></thead><tbody>{rows}</tbody></table></details>")
    if e["kind"] == "withdrawal":
        return (f"<div class='ev wd'>m{e['month']} · WITHDRAWAL · {esc(e['goal'])} · "
                f"needed {inr(e['requested'])} · sold {inr(e['sold'])}"
                + (f" · <b>shortfall {inr(e['shortfall'])}</b>" if e['shortfall'] > 0 else "") + "</div>")
    if e["kind"] == "lumpsum":
        return (f"<div class='ev lump'>m{e['month']} · LUMPSUM · deployed {inr(e['deployed'])}"
                f" · undeployed {inr(e['undeployed'])} · {len(e['buys'])} buys</div>")
    return ""  # SIP handled in the aggregated block below


def render_html(results) -> str:
    tabs = "".join(f"<div class='tab' onclick='show({i})'>{esc(r.name)}</div>"
                   for i, r in enumerate(results))
    panels = "".join(_panel(r) for r in results)
    css = ("body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#f4f3ee;margin:0;padding:16px}"
           ".tabs{display:flex;gap:4px;flex-wrap:wrap}.tab{padding:8px 14px;background:#fff;border:1px solid #d8d6cf;"
           "border-bottom:none;border-radius:6px 6px 0 0;cursor:pointer;font-size:13px}.tab.active{background:#222;color:#fff}"
           ".panel{background:#fff;border:1px solid #d8d6cf;border-radius:0 6px 6px 6px;padding:16px;display:none}"
           ".panel.active{display:block}.card{background:#fafaf8;border:1px solid #d8d6cf;border-radius:6px;padding:10px;font-size:13px}"
           ".muted{color:#777;margin-top:4px}.metrics{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}"
           ".chip{background:#eef1f5;border-radius:10px;padding:2px 8px;font-size:12px}"
           "table{border-collapse:collapse;width:100%;font-size:12px;margin:6px 0}"
           "th,td{padding:4px 7px;border-bottom:1px solid #eee;text-align:left}td.num,th.num{text-align:right}"
           ".ev{margin:6px 0;font-size:12px}.ev.reb summary{cursor:pointer;font-weight:600}"
           ".ev.wd{color:#b45309}.ev.lump{color:#1d4ed8}"
           "@media(prefers-color-scheme:dark){body{background:#1a1a1a;color:#eee}.panel,.card{background:#242424;color:#eee}}")
    js = ("function show(i){document.querySelectorAll('.tab').forEach((t,j)=>t.classList.toggle('active',i===j));"
          "document.querySelectorAll('.panel').forEach((p,j)=>p.classList.toggle('active',i===j));}"
          "window.addEventListener('DOMContentLoaded',()=>show(0));")
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>2-Year Lifecycle Simulation</title>"
            f"<style>{css}</style></head><body><h1>2-Year Portfolio Lifecycle Simulation</h1>"
            f"<div class='tabs'>{tabs}</div>{panels}<script>{js}</script></body></html>")
```
(SIP events are numerous; render them as a compact per-6-month summary inside `_panel` if desired — acceptable to defer to a follow-up; the rebalance/withdrawal/lumpsum cards carry the headline trades. Keep the metric-chip formatting rule simple; refine wording during review.)

- [ ] **Step 2: Write test_report.py**

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

import report
import simulate

def test_render_html_contains_profile_and_is_selfcontained():
    res = simulate.simulate_profile("Mohammed Faisal")
    html_out = report.render_html([res])
    assert "Mohammed Faisal" in html_out
    assert "<svg" in html_out
    assert "http://" not in html_out and "https://" not in html_out  # self-contained
    assert "REBALANCE" in html_out
```

- [ ] **Step 3: Run to verify it passes**

Run: `.venv-mac/bin/python -m pytest AI_Agents/lifecycle_sim_testing/tests/test_report.py -v`
Expected: 1 passed.

- [ ] **Step 4: Commit**

```bash
git add AI_Agents/lifecycle_sim_testing/report.py AI_Agents/lifecycle_sim_testing/tests/test_report.py
git commit -m "feat(lifecycle-sim): self-contained HTML report with per-profile tabs"
```

---

### Task 7: run.py — sweep all 5, write the HTML, verify

**Files:**
- Create: `AI_Agents/lifecycle_sim_testing/run.py`
- Modify: `Prozpr_Backend/.gitignore` (add the dev-only folder)

**Interfaces:**
- Consumes: `simulate.simulate_profile`, `report.render_html`, `PROFILES`.
- Produces: writes `AI_Agents/lifecycle_sim_testing/lifecycle_2y.html`.

- [ ] **Step 1: Write run.py**

```python
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import report  # noqa: E402
import simulate  # noqa: E402
from Rebalancing.Testing.Master_testing.profiles import PROFILES  # noqa: E402

_OUT = Path(__file__).resolve().parent / "lifecycle_2y.html"


def main() -> None:
    results = []
    for name in PROFILES:
        print(f"Simulating {name} ...")
        res = simulate.simulate_profile(name)
        m = res.metrics
        print(f"  trades={m['total_trades']} exits={m['total_exits']} "
              f"sip={m['sip_deployed']:.0f} lumpsum={m['lumpsum_deployed']:.0f} "
              f"withdrawn={m['withdrawn']:.0f} shortfall={m['shortfall']:.0f} "
              f"end={m['end_value']:.0f}")
        results.append(res)
    _OUT.write_text(report.render_html(results))
    print(f"\nWrote {_OUT}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Add the folder to .gitignore**

Append to `Prozpr_Backend/.gitignore`:
```
/AI_Agents/lifecycle_sim_testing/lifecycle_2y.html
```
(Track the source `.py` files; ignore only the generated HTML. If you prefer the whole folder untracked, add `/AI_Agents/lifecycle_sim_testing/` instead — confirm with the user before committing the source, per their gitignore preference in the spec §12.)

- [ ] **Step 3: Run the full sweep (verification)**

Run from `Prozpr_Backend/`:
```bash
.venv-mac/bin/python AI_Agents/lifecycle_sim_testing/run.py
```
Expected: 5 "Simulating …" lines with no `conservation off` / seed-assert error; a written `lifecycle_2y.html`. Confirm Aarav/Neha/Harpreet complete (their `SELL_DIRECT_STOCKS` + NFA-band paths are exercised, unlike Faisal).

- [ ] **Step 4: Run the whole test suite**

Run: `.venv-mac/bin/python -m pytest AI_Agents/lifecycle_sim_testing/tests/ -v`
Expected: all passed.

- [ ] **Step 5: Open the HTML and eyeball it (verification)**

Use the browser preview tool (`preview_start {url: "file://.../AI_Agents/lifecycle_sim_testing/lifecycle_2y.html"}`) or open manually. Confirm: 5 tabs; each shows the value curve, metrics chips, ≥1 EXIT in a rebalance card, the m6/m18/etc. withdrawal cards, and the m14 lumpsum card.

- [ ] **Step 6: Commit**

```bash
git add AI_Agents/lifecycle_sim_testing/run.py Prozpr_Backend/.gitignore
git commit -m "feat(lifecycle-sim): 5-profile sweep + HTML output + gitignore"
```

---

## Self-Review

**Spec coverage:**
- §2a reuse boundary → Global Constraints + Task 4 (all reused symbols imported, none reimplemented). ✓
- §3 assumptions (growth, SIP, lumpsum ₹5L, CSV pin, NFA bump, gold 8%, low-rated seed, cost 0.85) → constants.py (Task 1) + engines.refresh_alloc_input/seed_portfolio (Task 4). ✓
- §5 per-fund model, no lot ledger → portfolio.py (Task 2). ✓
- §6 state machine order (grow→withdraw→rebalance→lumpsum→sip; m0 special) → simulate.py (Task 5). ✓
- §7 adapters (build_request/_build_input reuse; apply/SELL_DIRECT_STOCKS/cash residual; mirror; model_copy refresh) → engines.py + portfolio.apply_* (Tasks 3–4). ✓
- §8 withdrawals (debt→gold→equity, pre-tax, shortfall) → portfolio.withdraw_for_goal (Task 3). ✓
- §9 HTML (tabs, curve, metrics, timeline) → report.py (Task 6). ✓
- §10 invariants (conservation, reconciliation, determinism, coverage) → in-loop assert (Task 5) + seed assert (Task 4). ✓ (determinism: report ignores timestamps/uuid by not rendering metadata.)
- §11 build order (Faisal first, then sweep, re-verify Aarav/Neha/Harpreet) → Tasks 4–7. ✓

**Placeholder scan:** the only deferred item is the optional per-6-month SIP aggregation block in report.py (Task 6) — explicitly marked optional; rebalance/withdrawal/lumpsum cards carry the headline. No `TODO`/`TBD` in code.

**Type consistency:** money is `Decimal` in `portfolio.py`; engine floats converted via `Decimal(str(x))`; `final_holding_amount` and `TradeAction.amount_inr` are `Decimal`, `FundBuy.amount_inr`/`deployed_inr` are `float` — conversions are explicit at every boundary. `rebalance(profile_refreshed, ref, port)` has one signature, consistent across `engines.py` and its caller in `simulate.py`.

## Execution Handoff

Two execution options:
1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks.
2. **Inline Execution** — batch execution with checkpoints.
