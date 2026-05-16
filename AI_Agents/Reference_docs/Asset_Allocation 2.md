# Ask Tilly · Asset Allocation Thesis

*Why we recommend the way we do*
*Version 1.1 · Internal & client reference · Last updated: May 2026*

---

> **Status note for follow-up questions:** This document is the client-facing thesis for the goal-based allocation engine that lives in `ailax/Prozpr_Backend/AI_Agents/src/asset_allocation_pydantic/`. As of v1.1 the **emergency fund**, **short-term goals** and **negative-NFA carve-out** sections have been rewritten to match the live engine. Other paragraphs (intergenerational override, multi-asset 65/35 framing, ₹16L hybrid gate, sector cap %s, "1% drop" rule, medium-term valuation overlay) still describe the *intent* of the thesis and may run ahead of what the code does today — for engine-true behaviour cross-check `tables.py` and the `steps/` files.

---

## The one-line thesis

We take care of overall wealth of our clients by understanding the professional and personal situation, and customise the right portfolio which can generate superior long term returns by selecting best-performing funds, reduce risks by diversifying in multiple asset classes, allocate strategically to meet your goals, and rebalance portfolios to take advantage of market imbalances and cash-flow needs. Our dynamic portfolios protect what you cannot afford to lose, grow what you can afford to invest, and stay inside guardrails for your long-term prosperity — without being held hostage to human biases. Your portfolio is an extension of your life, personality, and goals, and our allocation engine is built to ensure alignment.

## Five (well, seven) principles that drive every recommendation

| Principle | What it means in practice |
| --- | --- |
| **1. Alignment with your horizon** | We realise that you may need some money in the near term (1–2 years), the medium term (3–5 years), or to invest for long-term wealth creation. We ring-fence emergencies and known short-term needs before any market exposure is decided. We have seen many instances when people do an asset–liability or investment–cashflow mismatch which results in permanent losses of capital — for example selling equity when cash is needed urgently but markets are down. We avoid that scenario by construction. |
| **2. Blend your risk capacity and appetite to build the right allocation** | Our logic blends willingness to take risk with the ability to take risk. We assess what you can afford to risk from variables including income, expense, age, savings rate, property ownership, occupation stability and liabilities, and blend that with our assessment of your experience and psychology. When the two diverge sharply, we flag it rather than over-fit to either. The result is a customised portfolio rather than a standard product. |
| **3. Allocate according to your goals** | Goals vary by stage of life and unique need — wealth creation, regular income, children's education, buying a house — and the time at which each needs to be met. We allocate to less volatile assets for high-priority near-term goals, to a balance of equity and debt for medium-term goals, and to a balanced portfolio of asset classes for long-term life goals. The blend includes large-cap, mid-cap, small-cap and international equities, value and sector funds, dynamic-duration bonds, arbitrage funds, and gold/silver. We give complete visibility into how the portfolio is positioned for each goal, so you can adjust goals to align with your financial profile and risk tolerance. |
| **4. Superior returns with the right selection** | We select funds by evaluating more than 8,000 candidates against past returns, consistency, drawdowns, portfolio quality, manager longevity, fund-house reputation, and other variables — over a 10-year history. This allows selection without human bias, targeting better post-tax returns than the market for equity and better than fixed deposits for debt. Where active management does not add value, we recommend passive funds for the lower expense ratio. |
| **5. Dynamic portfolio management with contrarian calls on market cycles** | Markets go through up- and down-cycles. We evaluate valuations across asset classes, protect from excessive froth, and lean in when valuations cheapen. We are not timing markets for short-term gains; we are protecting the portfolio at extremes. The allocation logic is dynamic and factors market cycles and valuations rather than being static. |
| **6. Tax-efficient by default** | Tax outcomes are baked into the allocation, not bolted on. ELSS is allocated first when 80C headroom exists; hybrid funds replace pure debt for higher-effective-tax-rate clients to defer tax drag. We consider time horizon and the tax impact of selling securities at that horizon when picking funds. |
| **7. Guardrails over guesses** | Every allocation lives inside guardrails — published min/max bands for every asset class and subgroup. Market views nudge within the band, never breach it. No single view, no single client, gets a wild outlier portfolio; the engine strictly follows the guardrails. |

We are a holistic wealth advisor — not just a goal-based wealth tool, not just a risk-assessment tool, not just a mutual-fund comparison platform, and not just a tax-efficient advising tool.

## How a recommendation is built — in four deliberate steps

Every Ask Tilly recommendation is the output of four sequential, auditable steps. We can walk through any of them on demand.

### Step 1 — Score risk capacity and risk willingness

- Score the **ability** to take risk based on age, income, expense, savings rate, property ownership, occupation stability and liabilities.
- Score the **willingness** to take risk based on the behavioural questions answered at onboarding.
- Normalise the two scores at the midpoint. When the gap is significant — more than 30% of the range (i.e. > 3 points on the 1–10 scale) — a flag is raised and counselling is required to bridge the gap.
- The midpoint is the `effective_risk_score` used to build the allocation.

### Step 2 — Carve-outs for short-term needs: protect the near term first

- **Emergency fund:** 3 months of household expenses in overnight or liquid funds for clients with active income; 6 months when the client is living off the portfolio. The carve-out is reserved before any other bucket is allocated.
- **Short-term goals:** Outflows expected within 2 years are allocated 100% to fixed income — either a short-term debt fund, or an arbitrage fund when the client's effective tax rate is at or above the top slab (30%) for better post-tax outcomes. Within this two-year window we do not split by sub-horizon or risk capacity: the entire band is parked in debt to keep the corpus available on demand.
- **Negative net financial assets:** When the client's net financial assets are negative — i.e. liabilities (credit cards, personal loans and other near-term obligations) exceed liquid financial assets — the shortfall is added to the emergency carve-out and parked in debt before any equity exposure is contemplated.

**Why this matters:** any short-term need or expense must be kept in less risky, less volatile investments. Equity and commodities are volatile in the short term but more predictable in the long term. Match short-term goals with short-duration assets, and long-term goals with long-term investments. One of the most common reasons long-term portfolios fail is the forced sale at the wrong time. Carve-outs make that scenario structurally unlikely.

### Step 3 — Allocation for medium-term needs (2–5 years)

In the medium term, part of the portfolio is allocated to equity to generate higher returns and part to fixed income to reduce risk. Five years, even though it sounds long, does not always cover a full business cycle — so an investor can incur losses and miss medium-term goals if the buying price was high or the goal lands at the lower end of the cycle.

We adjust the equity/debt split for medium-term cash requirements based on the time horizon of the goal and the risk score of the client.

- **Equity exposure** in the medium term goes via equity-tilted hybrid (multi-asset) funds because (i) they are tax-efficient on the debt portion — only capital gains apply, and long-term capital-gains rates are lower than income-tax rates at higher slabs; (ii) the fund diversifies across large-cap, mid-cap, small-cap, and sometimes international equities and commodities; (iii) it reduces churn and transaction cost since the manager actively rebalances within the fund.
- **Debt exposure** in the medium term goes via arbitrage-plus-income funds when the client's effective tax rate is at or above 15% — these funds attract equity-style capital-gains taxation while delivering debt-like returns, lifting post-tax outcomes. Below 15%, plain debt is used.

### Step 4 — Long-term allocation: asset class → subgroup → guardrails

#### Step 4a — Asset-class allocation

The remaining corpus is allocated across **equities**, **debt** and **others** using the risk-score model. Conservative scores cap equity tightly; aggressive scores raise the equity ceiling and, at the very top of the risk scale, may bring the debt floor to zero.

- **Intergenerational-transfer override:** when the `intergenerational_transfer` flag is set, the engine boosts the effective risk score by +2.0 (capped at 9.0) to reflect the fact that the relevant horizon is the heir's, not the holder's. (The thesis intent is "use the profile of a 45-year-old"; today the engine implements it as a +2 score boost rather than a re-derived capacity.)
- **Market-commentary overlay:** we do detailed analysis of business cycles and segment valuations, take a house view on attractiveness across asset classes and subgroups, and nudge allocations *within* the bands relevant to each. Views never breach the bands.
- **Others gate:** at high risk scores (≥ 8.0) with a tepid view on others (≤ 6.0), the others slice is zeroed and the freed % is redistributed across equity and debt proportionally to their bands.

#### Step 4b — Subgroup allocation: where exactly the money goes

- **Phase A (ELSS first-pass):** if the client is in the **old tax regime** with unused 80C headroom, that headroom (up to ₹1.5L) is allocated to ELSS before any other equity decision — the tax saving compounds over the lock-in.
- **Phase B (multi-asset / tax-efficient hybrid):** an equity-tilted multi-asset fund is sized as the largest x such that x × equity_pct ≤ 50% of residual equity corpus AND x × debt_pct ≤ debt corpus. The default fund composition is **65% equity / 25% debt / 10% others** (the others slice is real and feeds the gold sleeve). The hybrid is preferred because: (i) tax efficiency on the debt portion (capital-gains taxation rather than slab income tax); (ii) diversification across equity subgroups and sometimes international equities and commodities; (iii) lower portfolio churn since the manager rebalances internally.
- **Phase C (residual equity subgroups):** the residual equity corpus (after ELSS and the multi-asset equity slice) is split across — low-beta large-cap funds for stability, medium-beta mid-cap funds for long-term growth, high-beta small-cap funds for high return/growth, US equities for exposure to the world's largest economy, value funds for contrarian bets at valuation extremes, and sector-focused funds for emerging structural themes. The split is bounded by per-subgroup min/max bands keyed to the risk score, and nudged within those bands by subgroup-level market views. Subgroups with view ≤ 7 are dropped (currently `value_equities` and `sector_equities` in the gate list). Subgroups whose share of residual equity falls below 8% are dropped and redistributed proportionally; within Phase 5 itself, an internal 2% threshold also strips slivers.
- **Debt subgroups:** any long-term debt amount, after the multi-asset's debt slice has been removed, goes into **arbitrage-plus-income** when the effective tax rate is ≥ 15%, otherwise into the plain debt subgroup.
- **Others / gold:** the residual others (after the multi-asset's others component) goes to gold/commodities.

#### Step 4c — Guardrails: validation before delivery

- Class totals sum to 100%; every subgroup sits inside its band; subgroup sums reconcile to the parent class (within a small ₹500 rounding tolerance to absorb independent round-to-₹100 operations).
- All allocations are rounded to whole-number percentages and rupee amounts to the nearest ₹100 — no fictitious precision.
- Step 6 explicitly validates Phase-1 asset-class bands and Phase-5 equity-subgroup bands (with a ±1pp tolerance on Phase-5 shares), and confirms every non-zero subgroup rolls up to a known asset class.

## Why a customer should trust this approach

| Question | Our answer |
| --- | --- |
| **Why is my equity at X% and not higher?** | One or more of the following may apply, depending on the case. (a) The risk score is lower — identify which input (age, income, occupation stability, liability, etc.) is dragging it and explain. (b) Short- and medium-term goals are being allocated first, and these need less volatile assets, which crowds out equity. (c) Our market view on equities is currently dovish, nudging the allocation toward the lower end of the band. |
| **Why so much in liquid / short debt?** | Same triage. (a) Lower risk score pushing toward debt. (b) Significant short- and medium-term goals consuming a large share of the corpus. (c) High share allocated to the emergency carve-out (3 months for active-income clients, 6 months for portfolio-livers). |
| **Why ELSS / hybrid funds in particular?** | Equity-tilted hybrid (multi-asset) funds: (i) tax efficiency on the debt portion (capital-gains taxation rather than slab income tax); (ii) diversification across large-cap, mid-cap, small-cap, sometimes international equity and commodities; (iii) lower churn and transaction cost — the manager rebalances within the fund. ELSS funds offer dual benefit of tax saving and wealth creation, with a 3-year lock-in and a deduction up to ₹1.5L under section 80C of the Income Tax Act 1961. |
| **Why won't you just chase the hot sector?** | Sector allocations are bounded — the per-risk-score equity-subgroup bands cap them to a band that, after multi-asset routing, typically lands at single-digit percentages of the total portfolio for high-risk clients only. We do not chase hot sectors; we allocate to sectors whose valuations have become cheap or to sectors with structural long-term tailwinds. |
| **What changes when markets change?** | Our market view nudges allocations in the direction of conviction *within* the bands — never beyond them. The view score is shown on every recommendation and we can explain why it was set the way it was. |

## What this thesis is — and is not

This document is a reference. It is not a prediction, not a guarantee of returns, and not a substitute for the actual recommendation, which is always personalised. The bands, formulas and overlays are reviewed periodically and may evolve as market structure and tax law change. When that happens, this thesis is updated and dated.

---

*Ask Tilly · Allocation Thesis v1.1 · Owner: Investment Research · Cycle: reviewed quarterly*
