# app/domains/execution/ — placing real mutual-fund orders

Two vendors, same job, deliberately independent: **Fintech Primitives** (Cybrilla)
and **MF Central** (CAMS + KFintech). Neither is a fallback for the other — they
have separate credentials, separate commercials and separate order books — and
nothing routes between them today.

## Layers

- **models/** — `fp_models.py` (`fp_exec_accounts`, `fp_exec_orders`) and
  `mfc_ft_order.py` (`mfc_ft_orders`).
- **schemas/** — `fp_schemas.py`, `mfc_ft_schemas.py`.
- **routers/** — `/fp` (Fintech Primitives), `/mfc-ft` (MF Central FT).
- **services/** — `fp_client` / `fp_service` / `fp_reports_service`;
  `mfc_ft_client` / `mfc_ft_service` / `mfc_masters`.

## Gotchas & invariants — MF Central FT

- **FT is the OUTBOUND half of the MFC integration**; `/mfc-cas` (in `ingestion`)
  is the inbound half. They share one protocol: `mfc_ft_client` owns no crypto
  and no HTTP, layering on `ingestion/services/mfc_client.MfcClient`, so the
  envelope is changed in exactly one place.
- **Every FT is a four-call chain with a human in the middle** — submit → OTP →
  consent → poll status; purchases add `ftPaymentUpdate`. It is four endpoints,
  not one, because a single call would block for as long as a person takes to
  read an SMS and would leave a closed tab nothing to poll.
- **Persist before calling MFC, never after** (`mfc_ft_service.place_order`
  commits the row before submitting). An order MFC accepts but whose response we
  lose must still be one we can name to them; the reverse order loses money we
  cannot identify.
- **All eight families share ONE envelope** and differ only in the scheme object,
  so `_envelope` is written once. The `otpSentTo` / `otpMobile` / `otpEmail`
  agreement is the reason: MFC rejects a mismatch with a 400 that names no field,
  and building the envelope per-family gets it right seven times out of eight.
- **A rejection can ride an HTTP 200.** KFintech answers `error: [...]`, CAMS
  `errors: [...]`, and `mfc_ft_client.extract_errors` reads both — code that
  branches on the status code alone reports a rejected order as placed.
- **`investorconsent` succeeds with 202 and an EMPTY body.** `MfcClient._unwrap`
  returns `{}` for a 202 so this reads as an outcome, not an anomaly.
- **A wrong OTP is not terminal.** MFC accepts a retry against the same `otpRef`,
  so `confirm_otp` leaves the order at `otp_sent`; failing it would strand an
  order over a typo.
- **`getFtTransactionStatus` is the only source of truth about an outcome.**
  Every earlier step reports that MFC accepted a request, which is not the same
  as an AMC allotting units. MFC documents no polling limit.
- **`status` and `rta_status` are both kept.** Ours is what code branches on;
  the registrar's own words are what a support desk recognises. Neither registrar
  publishes a closed list of phrasings, so `_map_rta_status` is substring-based
  and lossy on purpose.
- **AMC resolution tries containment before similarity** (`mfc_masters`). Our
  holdings carry SCHEME names ("HDFC Flexi Cap Fund - Regular Plan") and MFC
  wants a house code, so the house is a prefix of the input rather than the whole
  of it. It returns None rather than guessing — a wrong AMC submits the order to
  a different fund house.
- **Every FT field goes on the wire as a STRING**, amounts included; MFC's
  validator rejects a JSON number where it documents `String(n)`. Purchase
  amounts are whole rupees ("No decimals allowed").
- **A SIP is a purchase with a cadence** — MFC has no separate endpoint, so
  `frequency` is what turns `trxnType` FP into SIP (and AP into ASIP).
- **Redemption uses MFC's misspelt `userTrxNo`**; every other family says
  `userTrxnNo`. Correcting it silently drops the value.
- **SIP pause/cancel has a pre-flight** (`validateSipPauseCancel`), the only
  family that does. Skipping it turns a wrong `userTrxnNo` into a rejection after
  the investor has already been sent an OTP.
- **The mock (`ingestion/dev/mfc_ft_mock.py`) models three failure modes on
  purpose**: the empty-202 consent, a first poll that says "under process", and
  an amount ending `13` rejected inside a 200. Each is a way our client could be
  wrong that UAT would otherwise reveal late.

## Gotchas & invariants — Fintech Primitives

- See `fp_service.py`. The sandbox is rule-simulated and blocks every update
  verb, so everything is set inline at CREATE (including purchase consent).

## Don't read

- `__pycache__/`, `tests/`.
