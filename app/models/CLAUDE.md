# app/models/ — SQLAlchemy ORM classes

One file or subpackage per domain. Column-level detail lives in
`README_DATABASE_SCHEMA.md`.

## Child modules

- **profile/** — user profile tables: risk tolerance, tax, constraints, other
  assets, current properties, personal-finance / cashflow `ClientProfile` row.
- **goals/** — goals (legacy + cashflow-engine columns merged into one
  `goals` table), contributions, goal-holdings.
- **cashflow/** — cashflow-statement persistence: per-user inputs
  (assumptions, one-off events) plus per-run outputs (plan runs, annual /
  monthly rows, headline, fund flow summary, plan summary).
- **mf/** — MF ledger, SIPs, NAV, snapshots, fund lists.
- **stocks/** — equity transactions, prices, company metadata.

## Files at this level

- `user.py` — `User` hub table; relationships hang off it.
- `linked_account.py` — `LinkedAccount`.
- `family_member.py` — `FamilyMember`.
- `portfolio.py` — `Portfolio`, `PortfolioHolding`, `PortfolioAllocation`, and
  related portfolio tables.
- `chat.py` — chat sessions and messages.
- `chat_ai_module_run.py` — per-turn AI module telemetry rows.
- `chat_session_state.py` — per-session cross-turn state for chat handlers
  (e.g., `awaiting_save` gate used by the goal_planning counterfactual flow).
- `fund.py` — fund reference data.
- `ips.py` — investment policy statement records.
- `notification.py` — notification records.
- `meeting_note.py` — meeting note records.
- `rebalancing.py` — rebalancing recommendation records.
- `__init__.py` — imports every model so they register with `Base.metadata`.

## Don't read

- `__pycache__/`.
- `__init__.py` — bookkeeping imports only (covered by parent convention).
