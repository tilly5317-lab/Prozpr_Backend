# Test Users Guide

## Quick Start

```bash
cd Prozpr_Backend
python scripts/seed_test_users.py          # seed 10 users
python scripts/seed_test_users.py --reset   # wipe & re-seed
```

Requires: PostgreSQL running, `DATABASE_URL` set in `.env`.

---

## Credentials

| # | Mobile | Name | Net Worth | Risk Profile | Banks | Stocks | Password |
|---|--------|------|-----------|--------------|-------|--------|----------|
| 1 | `7770000001` | Aarav Sharma | 10L | Aggressive | 1 | RELIANCE, TCS | `Test@1234` |
| 2 | `7770000002` | Priya Patel | 25L | Mod. Aggressive | 1 | INFY, HDFCBANK | `Test@1234` |
| 3 | `7770000003` | Rohan Gupta | 50L | Moderate | 2 | RELIANCE, BHARTIARTL, ITC | `Test@1234` |
| 4 | `7770000004` | Sneha Reddy | 75L | Moderate | 2 | TCS, HDFCBANK, SBIN | `Test@1234` |
| 5 | `7770000005` | Vikram Singh | 1Cr | Mod. Aggressive | 2 | RELIANCE, INFY, ICICIBANK, TATAMOTORS | `Test@1234` |
| 6 | `7770000006` | Ananya Iyer | 1.5Cr | Moderate | 2 | RELIANCE, TCS, BHARTIARTL | `Test@1234` |
| 7 | `7770000007` | Karthik Nair | 2Cr | Mod. Conservative | 3 | HDFCBANK, ITC, WIPRO | `Test@1234` |
| 8 | `7770000008` | Meera Joshi | 3Cr | Conservative | 3 | RELIANCE, TCS, HDFCBANK, INFY | `Test@1234` |
| 9 | `7770000009` | Arjun Kapoor | 4Cr | Moderate | 3 | RELIANCE, TCS, INFY, BHARTIARTL, ICICIBANK | `Test@1234` |
| 10 | `7770000010` | Divya Menon | 5Cr | Conservative | 3 | RELIANCE, HDFCBANK, ITC, SBIN | `Test@1234` |

**Country code:** `+91` for all users.

---

## How to Login

### Option A: Frontend UI
1. Open the app (local: `http://localhost:8080`, deployed: your frontend URL).
2. Enter mobile number (e.g. `7770000001`) and password `Test@1234`.
3. Onboarding is already marked complete -- you go straight to the dashboard.

### Option B: API (Postman / curl)
```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"country_code": "+91", "mobile": "7770000001", "password": "Test@1234"}'
```
Response includes `access_token`. Use it as `Authorization: Bearer <token>`.

---

## Net Worth Breakdown Per User

Each user's net worth is split across **5 buckets** that sum to 100%:

| Bucket | What it means | Linked Account Type |
|--------|---------------|---------------------|
| **Cash** (10-22%) | Bank savings across 1-3 accounts | `bank_account` (HDFC, SBI, ICICI, Kotak, Axis) |
| **MF** (20-50%) | Mutual fund holdings (2-4 schemes) | `mutual_fund` (CAMS/KFintech) |
| **Stocks** (8-30%) | Direct equity (2-5 NSE stocks) | `stock_demat` (Zerodha) |
| **Debt** (5-40%) | Debt MF + bond allocation | Part of portfolio allocations |
| **Other** (5-12%) | Gold ETF, international funds | Part of portfolio allocations |

### Example: Vikram Singh (1Cr)
- Cash: 10L across 2 bank accounts (HDFC 5.5L, SBI 4.5L)
- MF: 35L in 3 equity MFs + 1 debt MF + 1 gold ETF
- Stocks: 25L in RELIANCE, INFY, ICICIBANK, TATAMOTORS
- Debt: 18L in debt MFs
- Other: 12L in gold ETF

---

## What Gets Populated Per User

| Table | What's Created |
|-------|---------------|
| `users` | Full profile: name, email, PAN, DOB, occupation, family status, currency=INR, onboarding=complete |
| `personal_finance_profiles` | Goals, income range, expenses, wealth sources, investment horizon |
| `risk_profiles` | Risk level (1-5), capacity, experience, drawdown tolerance, comfort assets |
| `investment_profiles` | Portfolio value, monthly savings, target corpus, income, liabilities, emergency fund |
| `investment_constraints` | Permitted/prohibited assets, leverage/derivatives flags |
| `asset_allocation_constraints` | Min/max allocation per asset class (Equity, Debt, Cash, Other) |
| `tax_profiles` | Income tax rate (varies by income bracket), capital gains rate |
| `review_preferences` | Quarterly review, market-drop triggers |
| **`linked_accounts`** | **1-3 bank accounts** (with realistic balances, IFSC, masked numbers) + **1 MF account** (CAMS) + **1 demat account** (Zerodha) |
| `portfolios` | Primary portfolio with total value = net worth |
| `portfolio_allocations` | Cash / Equity / Debt / Other buckets |
| **`portfolio_holdings`** | **MF holdings** (2-5 schemes: HDFC Flexi, ICICI Bluechip, SBI Small Cap, etc.) + **Stock holdings** (2-5 NSE stocks: RELIANCE, TCS, INFY, etc.) + **Bank deposit** holding |
| `portfolio_history` | 90 days of weekly portfolio value snapshots |
| `goals` | 1-2 goals (primary + emergency fund for NW >= 50L) |
| `goal_contributions` | Initial contribution per goal |
| `mf_fund_metadata` | 8 shared MF schemes |
| `mf_transactions` | 3 BUY transactions per MF holding (12mo, 6mo, 1mo ago) |
| **`company_metadata`** | 10 NSE blue-chip stocks (RELIANCE, TCS, INFY, HDFCBANK, etc.) |
| **`stock_transactions`** | 2 BUY transactions per stock holding (9mo, 3mo ago) |
| `notifications` | Welcome + quarterly review reminder |

---

## Linked Accounts Summary

### Banks (1-3 per user)
| Bank | IFSC | Users |
|------|------|-------|
| HDFC Bank | HDFC0001234 | All users (primary) |
| SBI | SBIN0005678 | Users 3-10 (2nd account) |
| ICICI Bank | ICIC0002345 | Users 7-10 (3rd account) |

Each bank account has:
- Realistic savings balance (proportional to NW)
- Masked account number
- IFSC code
- Active status with sync timestamps

### MF Account (1 per user)
- Provider: CAMS / KFintech
- Contains folio number, cost value, current value
- Linked to 2-5 mutual fund holdings

### Demat Account (1 per user)
- Provider: Zerodha
- Contains 2-5 stock holdings from NSE
- Each stock has buy transactions from 9 and 3 months ago

---

## Stock Catalog (10 blue-chips, shared)

| Symbol | Company | Current Price |
|--------|---------|--------------|
| RELIANCE | Reliance Industries | Rs.2,950 |
| TCS | Tata Consultancy Services | Rs.3,850 |
| INFY | Infosys | Rs.1,480 |
| HDFCBANK | HDFC Bank | Rs.1,720 |
| ICICIBANK | ICICI Bank | Rs.1,250 |
| BHARTIARTL | Bharti Airtel | Rs.1,680 |
| ITC | ITC Ltd | Rs.465 |
| SBIN | State Bank of India | Rs.820 |
| WIPRO | Wipro | Rs.480 |
| TATAMOTORS | Tata Motors | Rs.720 |

---

## MF Catalog (8 schemes, shared)

| Scheme | AMC | Category | Type |
|--------|-----|----------|------|
| HDFC Flexi Cap Fund | HDFC AMC | Equity | Flexi Cap |
| ICICI Pru Bluechip Fund | ICICI Pru | Equity | Large Cap |
| SBI Small Cap Fund | SBI MF | Equity | Small Cap |
| Parag Parikh Flexi Cap | PPFAS MF | Equity | Flexi Cap |
| Motilal Oswal Nasdaq 100 FOF | Motilal MF | Equity | International |
| Axis Liquid Fund | Axis MF | Debt | Liquid |
| Kotak Corporate Bond Fund | Kotak MF | Debt | Corporate Bond |
| Nippon India Gold ETF | Nippon MF | Other | Gold ETF |

---

## Re-seeding / Resetting

```bash
python scripts/seed_test_users.py --reset
```

This deletes all 10 test users (cascading to all child tables) and re-inserts everything fresh. Safe to run multiple times. Idempotent UUIDs (deterministic from mobile number).

---

## Notes for Testers

1. **These users bypass OTP** -- password login is used directly.
2. **Portfolio data is synthetic** -- stock prices are approximate, NAVs are deterministic.
3. **All NW buckets sum to 100%** -- Cash + MF + Stocks + Debt + Other = Net Worth.
4. **Linked accounts have realistic metadata** -- balances, IFSC codes, masked identifiers, sync timestamps.
5. **Tokens expire in 7 days** from seed time. Re-run the script or login again to get fresh tokens.
6. **Do NOT use in production** -- PAN numbers, emails, and account numbers are clearly fake.
