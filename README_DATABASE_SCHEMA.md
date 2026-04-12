# Database schema — full table reference (user journey order)

PostgreSQL. Models: `app/models/`. Apply migrations: `alembic upgrade head` (from this folder).

This document lists **every physical table** in the app metadata, in the rough order a user moves through the product: **signup → profile → suitability → portfolio & invest → ongoing servicing**. **Views** are at the end.

**Note on “orders”:** There is no separate `orders` table. A **lump-sum buy/sell** is recorded as rows in **`mf_transactions`** / **`stock_transactions`** after execution; a **SIP** is a row in **`mf_sip_mandates`** that should generate **`mf_transactions`** on each debit date. **`funds`** is a **catalog** for discovery/invest UI, not an order book.

---

## Journey map (quick)

| Step | Tables |
|------|--------|
| 1 Login / account | `users` |
| 2 Onboarding & identity | `user_profiles`, `family_members` |
| 3 Link brokers / banks | `linked_accounts` |
| 4 Risk & experience | `risk_profiles` |
| 5 Objectives & money picture | `investment_profiles` |
| 6 Rules, tax, review cadence | `investment_constraints`, `asset_allocation_constraints`, `tax_profiles`, `review_preferences` |
| 7 Goals (structured) | `goals`, `goal_contributions`, `goal_holdings` |
| 8 IPS document | `investment_policy_statements` |
| 9 Fund discovery (catalog) | `funds` |
| 10 Dashboard portfolio | `portfolios`, `portfolio_allocations`, `portfolio_holdings`, `portfolio_history` |
| 11 Rebalance suggestions | `rebalancing_recommendations` |
| 12 Operational MF + SIP | `mf_fund_metadata`, `mf_nav_history`, `mf_transactions`, `mf_sip_mandates` |
| 13 Other assets | `other_investments` |
| 14 Direct equity | `company_metadata`, `stock_price_history`, `stock_transactions` |
| 15 Model vs actual allocation | `portfolio_allocation_snapshots`, `user_investment_lists` |
| 16 Chat & advisor | `chat_sessions`, `chat_messages`, `meeting_notes`, `meeting_note_items`, `notifications` |
| 17 Roll-ups | Views: `mf_holdings`, `stock_holdings`, `net_worth_summary` |

---

## 1. Login & account

### `users`

**Purpose:** Core identity and auth. Everything hangs off `user_id`.

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | Primary key |
| `email` | VARCHAR(320), unique | Login / comms (nullable) |
| `country_code` | VARCHAR(10) | Phone prefix |
| `mobile` | VARCHAR(20) | Mobile |
| `phone` | VARCHAR(32), unique | Canonical phone string |
| `password_hash` | VARCHAR(255) | Hashed password (nullable e.g. OTP-only) |
| `first_name` / `last_name` | VARCHAR(100) | Name |
| `is_active` | BOOLEAN | Account enabled |
| `is_onboarding_complete` | BOOLEAN | Profile funnel done |
| `created_at` / `updated_at` | TIMESTAMPTZ | Audit |

---

## 2. Onboarding & household

### `user_profiles`

**Purpose:** “About you” + extended profile (one row per user).

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `user_id` | UUID FK → `users`, unique | Owner |
| `date_of_birth` | DATE | Age / suitability |
| `selected_goals` | JSONB | High-level goal tags from onboarding |
| `custom_goals` | JSONB | Free-form goal strings |
| `investment_horizon` | VARCHAR(50) | Stated horizon |
| `annual_income_min` / `_max` | NUMERIC(15,2) | Income band |
| `annual_expense_min` / `_max` | NUMERIC(15,2) | Expense band |
| `occupation` | VARCHAR(100) | Job context |
| `family_status` | VARCHAR(100) | Household |
| `wealth_sources` | JSONB | e.g. salary, business |
| `personal_values` | JSONB | Preferences |
| `address` | VARCHAR(500) | Contact |
| `currency` | VARCHAR(3) | Display currency (default GBP in model) |
| `created_at` / `updated_at` | TIMESTAMPTZ | Audit |

### `family_members`

**Purpose:** Household linking (owner invites member; OTP-style status).

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `owner_id` | UUID FK → `users` | Primary user |
| `member_user_id` | UUID FK → `users`, nullable | Linked account once verified |
| `nickname` | VARCHAR(120) | Label |
| `email` / `phone` | VARCHAR | Contact |
| `relationship_type` | VARCHAR(30) | e.g. spouse |
| `status` | VARCHAR(20) | e.g. pending_otp |
| `created_at` / `updated_at` | TIMESTAMPTZ | Audit |

Unique: (`owner_id`, `member_user_id`).

---

## 3. Linked external accounts

### `linked_accounts`

**Purpose:** MF / bank / demat connections for sync or future aggregation.

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `user_id` | UUID FK → `users` | Owner |
| `account_type` | ENUM | mutual_fund, bank_account, stock_demat, other |
| `provider_name` | VARCHAR(255) | Institution |
| `account_identifier` | VARCHAR(255) | External ref |
| `encrypted_access_token` | TEXT | Stored token |
| `status` | ENUM | pending, active, inactive, failed |
| `metadata` | JSONB | Extra provider payload (column name `metadata` in DB) |
| `linked_at` / `last_synced_at` | TIMESTAMPTZ | Lifecycle |
| `created_at` / `updated_at` | TIMESTAMPTZ | Audit |

---

## 4. Risk profile

### `risk_profiles`

**Purpose:** Risk tolerance, capacity, horizon for advice and guardrails.

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `user_id` | UUID FK → `users`, unique | Owner |
| `risk_level` | INT | 0–4 → Conservative … Aggressive |
| `risk_capacity` | VARCHAR(50) | Capacity label |
| `investment_experience` | VARCHAR(100) | Experience |
| `investment_horizon` | VARCHAR(50) | Horizon text |
| `drop_reaction` | VARCHAR(100) | Behavioural |
| `max_drawdown` | NUMERIC(5,2) | Comfort drawdown % |
| `comfort_assets` | JSONB | Allowed sleeves |
| `created_at` / `updated_at` | TIMESTAMPTZ | Audit |

---

## 5. Investment profile (objectives & balance sheet)

### `investment_profiles`

**Purpose:** Objectives, corpus targets, income, liabilities, liquidity (IPS input).

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `user_id` | UUID FK → `users`, unique | Owner |
| `objectives` | JSONB | Objective tags |
| `detailed_goals` | JSONB | Structured goal snippets |
| `portfolio_value` | NUMERIC(15,2) | Declared portfolio |
| `monthly_savings` | NUMERIC(15,2) | Savings rate |
| `target_corpus` / `target_timeline` | NUMERIC / VARCHAR | Target planning |
| `annual_income` | NUMERIC(15,2) | Income |
| `retirement_age` | INT | Target retirement |
| `investable_assets` | NUMERIC(15,2) | Investable AUM |
| `total_liabilities` | NUMERIC(15,2) | Debt |
| `property_value` / `mortgage_amount` | NUMERIC(15,2) | Property |
| `expected_inflows` / `regular_outgoings` / `planned_major_expenses` | NUMERIC(15,2) | Cash flow |
| `emergency_fund` / `emergency_fund_months` | NUMERIC / VARCHAR | Buffer |
| `liquidity_needs` | TEXT | Narrative |
| `income_needs` | NUMERIC(15,2) | Income from portfolio |
| `is_multi_phase_horizon` / `phase_description` / `total_horizon` | BOOL / TEXT / VARCHAR | Phased horizon |
| `created_at` / `updated_at` | TIMESTAMPTZ | Audit |

---

## 6. Constraints, tax, review

### `investment_constraints`

**Purpose:** Hard/soft rules (permitted assets, bans on leverage/derivatives).

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `user_id` | UUID FK → `users`, unique | Owner |
| `permitted_assets` | JSONB | Allow-list |
| `prohibited_instruments` | JSONB | Block-list |
| `is_leverage_allowed` | BOOLEAN | Leverage flag |
| `is_derivatives_allowed` | BOOLEAN | Derivatives flag |
| `diversification_notes` | TEXT | Narrative |
| `created_at` / `updated_at` | TIMESTAMPTZ | Audit |

### `asset_allocation_constraints`

**Purpose:** Min/max % per asset class under a parent constraint.

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `constraint_id` | UUID FK → `investment_constraints` | Parent |
| `asset_class` | VARCHAR(100) | Sleeve key |
| `min_allocation` / `max_allocation` | NUMERIC(5,2) | % band |
| `created_at` | TIMESTAMPTZ | Audit |

### `tax_profiles`

**Purpose:** Tax rates for after-tax advice.

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `user_id` | UUID FK → `users`, unique | Owner |
| `income_tax_rate` | NUMERIC(5,2) | Income tax % |
| `capital_gains_tax_rate` | NUMERIC(5,2) | CGT % |
| `notes` | TEXT | Extra |
| `created_at` / `updated_at` | TIMESTAMPTZ | Audit |

### `review_preferences`

**Purpose:** How often / why to review the plan.

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `user_id` | UUID FK → `users`, unique | Owner |
| `frequency` | VARCHAR(50) | e.g. quarterly |
| `triggers` | JSONB | e.g. drawdown |
| `update_process` | TEXT | Workflow |
| `created_at` / `updated_at` | TIMESTAMPTZ | Audit |

---

## 7. Structured goals (goal planner)

### `goals`

**Purpose:** Named goals with type, today’s cost, inflation, deadline (feeds AI + APIs).

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `user_id` | UUID FK → `users` | Owner |
| `goal_name` | VARCHAR(100) | Name |
| `goal_type` | ENUM | RETIREMENT, CHILD_EDUCATION, … OTHER |
| `present_value_amount` | NUMERIC(15,2) | Today’s cost ₹; > 0 |
| `inflation_rate` | NUMERIC(5,2) | 0–50%, default 6 |
| `target_date` | DATE | Deadline |
| `priority` | ENUM | PRIMARY, SECONDARY |
| `status` | ENUM | ACTIVE, ACHIEVED, PAUSED, ABANDONED |
| `notes` | TEXT | Free text |
| `created_at` / `updated_at` | TIMESTAMPTZ | Audit |

### `goal_contributions`

**Purpose:** Cash contributions toward a goal.

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `goal_id` | UUID FK → `goals` | Goal |
| `amount` | NUMERIC(15,2) | ₹ |
| `contributed_at` | TIMESTAMPTZ | When |
| `note` | TEXT | Optional |
| `created_at` | TIMESTAMPTZ | Audit |

### `goal_holdings`

**Purpose:** Optional notional line items per goal (UI/planner), not the operational MF ledger.

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `goal_id` | UUID FK → `goals` | Goal |
| `fund_name` | VARCHAR(255) | Label |
| `category` | VARCHAR(100) | Tag |
| `invested_amount` | NUMERIC(15,2) | Cost |
| `current_value` | NUMERIC(15,2) | Value |
| `gain_percentage` | NUMERIC(7,2) | Display |
| `created_at` / `updated_at` | TIMESTAMPTZ | Audit |

---

## 8. Investment Policy Statement

### `investment_policy_statements`

**Purpose:** Generated IPS JSON per user/version.

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `user_id` | UUID FK → `users` | Owner |
| `version` | INT | Revision |
| `status` | VARCHAR(20) | e.g. draft |
| `content` | JSONB | Full IPS payload |
| `created_at` / `updated_at` | TIMESTAMPTZ | Audit |

---

## 9. Fund discovery (catalog — before you “invest”)

### `funds`

**Purpose:** Searchable catalog for Discover / Invest screens (`user_id` NULL = global house list).

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `user_id` | UUID FK → `users`, nullable | Scoped fund vs global |
| `name` / `short_name` | VARCHAR | Names |
| `ticker_symbol` | VARCHAR(20) | Symbol |
| `category` / `sector` | VARCHAR | Classification |
| `description` | TEXT | Blurb |
| `exchange` | VARCHAR(50) | Venue |
| `expense_ratio` | NUMERIC(5,4) | Ongoing charge |
| `exit_load` | VARCHAR(100) | Load text |
| `min_investment` | NUMERIC(15,2) | Minimum |
| `return_1y` / `3y` / `5y` | NUMERIC(7,2) | Display returns |
| `risk_level` | VARCHAR(20) | Risk tag |
| `is_trending` / `is_house_view` | BOOLEAN | Merchandising |
| `created_at` / `updated_at` | TIMESTAMPTZ | Audit |

---

## 10. Dashboard portfolio (summary layer)

### `portfolios`

**Purpose:** Primary (or named) portfolio shell for dashboard totals.

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `user_id` | UUID FK → `users` | Owner |
| `name` | VARCHAR(255) | e.g. Primary |
| `total_value` / `total_invested` | NUMERIC(15,2) | Roll-up numbers |
| `total_gain_percentage` | NUMERIC(7,2) | Display |
| `is_primary` | BOOLEAN | Main portfolio |
| `created_at` / `updated_at` | TIMESTAMPTZ | Audit |

### `portfolio_allocations`

**Purpose:** Target or current sleeve mix on the dashboard.

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `portfolio_id` | UUID FK → `portfolios` | Parent |
| `asset_class` | VARCHAR(100) | Sleeve |
| `allocation_percentage` | NUMERIC(5,2) | % |
| `amount` | NUMERIC(15,2) | ₹ |
| `performance_percentage` | NUMERIC(7,2) | Sleeve return |
| `created_at` / `updated_at` | TIMESTAMPTZ | Audit |

### `portfolio_holdings`

**Purpose:** Line-level positions as shown on dashboard (simplified vs full MF ledger).

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `portfolio_id` | UUID FK → `portfolios` | Parent |
| `instrument_name` | VARCHAR(255) | Name |
| `instrument_type` | VARCHAR(50) | e.g. mutual_fund |
| `ticker_symbol` | VARCHAR(20) | Symbol |
| `quantity` / `average_cost` / `current_price` | NUMERIC | Position |
| `current_value` | NUMERIC(15,2) | MV |
| `allocation_percentage` | NUMERIC(5,2) | Weight |
| `exchange` | VARCHAR(50) | Venue |
| `expense_ratio` | NUMERIC(5,4) | Fee |
| `return_1y` / `3y` / `5y` | NUMERIC(7,2) | Display |
| `created_at` / `updated_at` | TIMESTAMPTZ | Audit |

### `portfolio_history`

**Purpose:** Time series of total portfolio value.

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `portfolio_id` | UUID FK → `portfolios` | Parent |
| `recorded_date` | DATE | As-of |
| `total_value` | NUMERIC(15,2) | Value |
| `created_at` | TIMESTAMPTZ | Insert |

---

## 11. Rebalancing (post-allocation)

### `rebalancing_recommendations`

**Purpose:** Suggested trades vs current `portfolio` (workflow: pending → approved → executed).

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `portfolio_id` | UUID FK → `portfolios` | Target portfolio |
| `status` | ENUM | pending, approved, executed, rejected |
| `recommendation_data` | JSONB | Structured deltas |
| `reason` | TEXT | Narrative |
| `created_at` / `updated_at` | TIMESTAMPTZ | Audit |

---

## 12. Operational mutual funds (after orders execute)

**Join key:** `scheme_code` (AMFI). **`mf_nav_history.scheme_code` → `mf_fund_metadata.scheme_code`** (metadata is parent).

### `mf_fund_metadata`

**Purpose:** One row per scheme; category drives Equity/Debt/Hybrid in net worth.

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `scheme_code` | VARCHAR(20), unique | Join key |
| `scheme_name` | VARCHAR(200) | Name |
| `amc_name` | VARCHAR(100) | AMC |
| `category` | VARCHAR(50) | Equity / Debt / Hybrid / … |
| `sub_category` | VARCHAR(100) | e.g. Large Cap |
| `plan_type` | ENUM | DIRECT, REGULAR |
| `option_type` | ENUM | GROWTH, IDCW |
| `is_active` | BOOLEAN | Scheme live |
| *(many nullable analytics cols)* | strings / numeric | SEBI fields, ratings, loads, cap split %, return % 1y–10y |
| `created_at` / `updated_at` | TIMESTAMPTZ | Audit |

### `mf_nav_history`

**Purpose:** Daily NAV feed.

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `scheme_code` | VARCHAR(20) FK → metadata | Scheme |
| `isin` | VARCHAR(20) | ISIN |
| `scheme_name` | VARCHAR(200) | Name on row |
| `mf_type` | VARCHAR(200) | Source label |
| `nav` | NUMERIC(12,4) | NAV |
| `nav_date` | DATE | Date |
| `created_at` | TIMESTAMPTZ | Insert |

Unique: (`scheme_code`, `nav_date`).

### `mf_transactions`

**Purpose:** **Executed** MF events (source of truth for holdings).

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `user_id` | UUID FK → `users` | Investor |
| `scheme_code` | VARCHAR(20) FK | Fund |
| `sip_mandate_id` | UUID FK → `mf_sip_mandates`, nullable | If from SIP |
| `folio_number` | VARCHAR(30) | Folio |
| `transaction_type` | ENUM | BUY, SELL, SWITCH_IN, SWITCH_OUT, DIVIDEND_REINVEST |
| `transaction_date` | DATE | Trade date |
| `units` | NUMERIC(18,4) | Signed units |
| `nav` | NUMERIC(12,4) | NAV at trade |
| `amount` | NUMERIC(15,2) | ₹ |
| `stamp_duty` | NUMERIC(10,2) | Buy duty |
| `created_at` | TIMESTAMPTZ | Insert |

### `mf_sip_mandates`

**Purpose:** **Standing instruction** (“order template”); runtime posts `mf_transactions`.

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `user_id` | UUID FK → `users` | Owner |
| `scheme_code` | VARCHAR(20) FK | Fund |
| `folio_number` | VARCHAR(30) | Optional until first unit |
| `sip_amount` | NUMERIC(15,2) | Instalment ₹ |
| `frequency` | ENUM | MONTHLY, QUARTERLY |
| `debit_day` | INT | 1–28 |
| `start_date` / `end_date` | DATE | Window |
| `stepup_amount` / `stepup_percentage` | NUMERIC | Step-up |
| `stepup_frequency` | ENUM | ANNUALLY, HALF_YEARLY |
| `status` | ENUM | ACTIVE, PAUSED, CANCELLED, COMPLETED |
| `created_at` / `updated_at` | TIMESTAMPTZ | Audit |

---

## 13. Other investments (non-MF, non-stock)

### `other_investments`

**Purpose:** FD, PPF, EPF, NPS, bonds, SGB, insurance, real estate snapshots.

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `user_id` | UUID FK → `users` | Owner |
| `investment_type` | VARCHAR(50) | FD, PPF, NPS, REAL_ESTATE, … |
| `investment_name` | VARCHAR(200) | Label |
| `present_value` | NUMERIC(15,2) | Value ₹ |
| `as_of_date` | DATE | Valuation date |
| `maturity_date` | DATE | Optional |
| `status` | ENUM | ACTIVE, MATURED, WITHDRAWN, CLOSED |
| `notes` | TEXT | Policy #, rate, etc. |
| `created_at` / `updated_at` | TIMESTAMPTZ | Audit |

---

## 14. Direct equity

### `company_metadata`

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `symbol` | VARCHAR(50), unique | Ticker |
| `company_name` | VARCHAR(200) | Name |
| `exchange` | VARCHAR(20) | Optional |
| `created_at` / `updated_at` | TIMESTAMPTZ | Audit |

### `stock_price_history`

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `symbol` | VARCHAR(50) FK | Ticker |
| `price_date` | DATE | Session |
| `close_price` | NUMERIC(15,4) | Close |
| `created_at` | TIMESTAMPTZ | Insert |

Unique: (`symbol`, `price_date`).

### `stock_transactions`

**Purpose:** **Executed** equity trades.

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `user_id` | UUID FK → `users` | Owner |
| `symbol` | VARCHAR(50) FK | Security |
| `transaction_type` | ENUM | BUY, SELL |
| `transaction_date` | DATE | Trade date |
| `quantity` | NUMERIC(18,4) | Shares |
| `price` | NUMERIC(15,4) | Price |
| `amount` | NUMERIC(15,2) | Notional ₹ |
| `created_at` | TIMESTAMPTZ | Insert |

---

## 15. Model allocation & compliance lists

### `portfolio_allocation_snapshots`

**Purpose:** History of ideal / suggested / actual allocation JSON.

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `user_id` | UUID FK → `users` | Owner |
| `snapshot_kind` | ENUM | IDEAL, SUGGESTED, ACTUAL |
| `allocation` | JSONB | Weights / sleeves |
| `effective_at` | TIMESTAMPTZ | As-of |
| `source` | VARCHAR(100) | e.g. model |
| `notes` | TEXT | Optional |
| `created_at` | TIMESTAMPTZ | Insert |

### `user_investment_lists`

**Purpose:** Per-user JSON: illiquid+exit load, STCG, restricted schemes.

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `user_id` | UUID FK → `users` | Owner |
| `list_kind` | ENUM | ILLIQUID_EXIT, STCG, RESTRICTED |
| `entries` | JSONB | Array of objects |
| `created_at` / `updated_at` | TIMESTAMPTZ | Audit |

Unique: (`user_id`, `list_kind`).

---

## 16. Chat, meetings, notifications

### `chat_sessions` / `chat_messages`

**Purpose:** In-app AI / support threads.

**`chat_sessions`:** `id`, `user_id` FK, `title`, `status` (active/closed), `created_at`, `updated_at`.

**`chat_messages`:** `id`, `session_id` FK, `role` (user/assistant), `content` TEXT, `created_at`.

### `meeting_notes` / `meeting_note_items`

**Purpose:** Advisor meeting record.

**`meeting_notes`:** `id`, `user_id` FK, `title`, `meeting_date`, `is_mandate_approved`, timestamps.

**`meeting_note_items`:** `id`, `meeting_note_id` FK, `item_type` (transcript/summary), `role`, `content`, `sort_order`, `created_at`.

### `notifications`

**Purpose:** In-app alerts.

| Column | Type | Role |
|--------|------|------|
| `id` | UUID | PK |
| `user_id` | UUID FK | Owner |
| `title` | VARCHAR(255) | Headline |
| `message` | TEXT | Body |
| `notification_type` | VARCHAR(50) | Category |
| `is_read` | BOOLEAN | Read flag |
| `action_url` | VARCHAR(500) | Deep link |
| `created_at` | TIMESTAMPTZ | Insert |

---

## 17. Views (read-only roll-ups)

### `mf_holdings`

Net MF position per user + `scheme_code` + `folio`: units, invested (BUY amounts only), latest NAV, `current_value`, `unrealised_pnl`, metadata `category` / `sub_category` / `amc_name`. Excludes zero units.

### `stock_holdings`

Net equity per user + symbol at latest close; `quantity`, invested, `current_value`, `unrealised_pnl`.

### `net_worth_summary`

One row per user: MF split Equity/Debt/Hybrid, stock value, `other_investments` buckets (FD/RD, PPF/EPF/VPF, NPS, BOND, SGB, INSURANCE, REAL_ESTATE), `total_invested` (MF+stock cost), `total_current_value`, `total_unrealised_pnl`, `last_updated` (max of NAV / stock / other as-of dates).

---

## Table count summary

| Kind | Count |
|------|------|
| Physical tables | 35 |
| Views | 3 |

**Physical tables:** `users`, `user_profiles`, `family_members`, `linked_accounts`, `risk_profiles`, `investment_profiles`, `investment_constraints`, `asset_allocation_constraints`, `tax_profiles`, `review_preferences`, `goals`, `goal_contributions`, `goal_holdings`, `investment_policy_statements`, `funds`, `portfolios`, `portfolio_allocations`, `portfolio_holdings`, `portfolio_history`, `rebalancing_recommendations`, `mf_fund_metadata`, `mf_nav_history`, `mf_transactions`, `mf_sip_mandates`, `other_investments`, `company_metadata`, `stock_price_history`, `stock_transactions`, `portfolio_allocation_snapshots`, `user_investment_lists`, `chat_sessions`, `chat_messages`, `meeting_notes`, `meeting_note_items`, `notifications`.

For enum value lists on MF/goal/other tables, see `app/models/*.py` or the Alembic migration `alembic/versions/e4f8a2b1c901_fintech_goals_mf_networth_schema.py`.
