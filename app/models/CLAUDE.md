# app/models/ — SQLAlchemy ORM classes

One file or subpackage per domain. Column-level detail lives in
`README_DATABASE_SCHEMA.md`.

## Child modules

- **profile/** — user profile tables: risk tolerance, tax, constraints, other
  assets.
- **goals/** — goals, contributions, goal-holdings.
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
- `fund.py` — fund reference data.
- `ips.py` — investment policy statement records.
- `notification.py` — notification records.
- `meeting_note.py` — meeting note records.
- `rebalancing.py` — rebalancing recommendation records.
- `__init__.py` — imports every model so they register with `Base.metadata`.

## Don't read

- `__pycache__/`.
- `__init__.py` — bookkeeping imports only (covered by parent convention).
