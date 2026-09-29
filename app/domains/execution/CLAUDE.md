# app/domains/execution/ — order execution via Fintech Primitives (FP)

The rails that actually place mutual-fund orders: investor setup, KYC gate, lumpsum and SIP purchases, payment, order status, and folio/returns reporting.

## Entry / contract
- HTTP only: `fp_router` (registered in `app/routers/__init__.py`) — KYC gate, setup, lumpsum, SIP, order status, folios, investment report.
- All outbound FP traffic goes through `get_fp_client()`; the client is **inert** unless `FP_TENANT` / `FP_API_KEY` / `FP_API_SECRET` are set (`Settings.fp_enabled()`).

## Layers
- **models/** — `fp_models.py`: one FP account row per user, one row per order.
- **schemas/** — setup, order, folio and investment-report payloads.
- **routers/** — `fp_router.py`.
- **services/** — `fp_client` (auth + transport), `fp_service` (setup/KYC/order bodies), `fp_reports_service` (folios + returns).

## Gotchas & invariants
- **Auth is a FORM post, not JSON.** `POST {base}/v2/auth/{tenant}/token` with `grant_type=client_credentials&client_id=…&client_secret=…`; JSON to the token endpoint is a 400. Every call then carries `Authorization: Bearer …` **and** `x-tenant-id` (`services/fp_client.py`).
- **Setup is all-at-CREATE — the sandbox gateway blocks every update verb.** The chain (investor profile → email → phone → address → bank account → MF investment account) must carry every field on the way in; there is no second pass to fix an omission (`services/fp_service.py`).
- **The address `nature` field is mandatory or orders die at the gateway** — not at address creation, which accepts the row, but later at order placement. A failure here reads as a broken order, not a broken address.
- **Order consent must ride INLINE on the purchase body**, for the same reason: updates are blocked. `scheme` is an **ISIN**, and `user_ip` is mandatory.
- **Order statuses are FP's own state strings, stored verbatim** (`under_review`, `created`, …) — deliberately no local enum to drift against FP's lifecycle (`models/fp_models.py`).
- **`simulated=True` data is NOT holdings.** `fp_reports_service` calls the live FP API first; our sandbox returns empty/404 because a folio is only minted once a purchase settles and nothing settles there, so it falls back to clearly-labelled test data priced with real NAVs from `mf_nav_history`. The flag flips to `False` the moment FP returns real folios — never treat a simulated row as a real holding.
- **KYC readiness is a column, not a call.** The account row is created at signup with `kyc_status='pending'` and flips to `completed` / `failed` once the user submits their PAN.

## Don't read
- `__pycache__/`, `tests/`.
