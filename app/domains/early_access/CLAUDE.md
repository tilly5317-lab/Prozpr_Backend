# domains/early_access/ — invite-only launch: the /earlyaccess waiting list

Prozpr is onboarding its first 100 users by hand. Two halves live here:

1. **The front door is shut.** A phone number with no account behind it cannot
   create one — `/auth/signup` answers 403 and the app's entry screen sends the
   visitor to the public `/earlyaccess` page instead. Returning users sign in
   exactly as before.
2. **The waiting list.** `/earlyaccess` posts one application per person to the
   team's Google Sheet, which is the only place these leads are recorded.

## Entry / contract

Three **public, unauthenticated** endpoints (a prospective user has no account
yet, which is the point). All under `API_V1_PREFIX` — `/api/v1/early-access/…`.

| Route | Purpose |
| --- | --- |
| `POST /early-access/signup` | One application. 201 with the seat it took. |
| `GET /early-access/seats` | The live seat meter. 503 if the Sheet is unreadable. |
| `GET /early-access/signup-status` | Whether the app should offer account creation at all. |

Request body (`EarlyAccessSignupRequest`) — **field names are the agreed
contract with the frontend's `/earlyaccess` page; do not rename on one side**:

```jsonc
{ "name": "Ananya Rao", "email": "ananya@example.com",
  "whatsapp": "+91 98765 43210",      // optional, free-form, as typed
  "profession": "Finance",            // one of schemas.PROFESSIONS
  "source": "earlyaccess_page" }
```

Response: `{ ok, seats_total, seats_claimed, seats_left, waitlisted,
already_registered }`. A repeat email is a **success**, not an error —
`already_registered: true` with the seat that address already holds; the page
renders it as "You're already on the list".

## Files

- `routers/early_access_router.py` — the three routes; honeypot, per-IP window,
  seat arithmetic, and the 503/429 mapping.
- `schemas/early_access.py` — request/response shapes and `PROFESSIONS`. No ORM
  model exists; the register is a Sheet, not a table.
- `services/early_access_service.py` — every call to the Apps Script web app,
  the seat cache, and the optional Slack ping.

## Gotchas & invariants

- **The Sheet is the sole register** (`early_access_service` module docstring).
  There is no table and no local fallback, so `append_application` raises on
  every failure mode and the router answers 503 — an applicant is never told
  they are on the list when no row exists. Same arrangement as the issue
  register in `support/services/issue_report_service`.
- **The seat count is read back from the Sheet**, not counted here
  (`fetch_claimed`). A counter on this side would drift the first time someone
  edits the Sheet by hand, which is exactly what the team will do while working
  the list. Cached 30s so a public page cannot burn the Apps Script quota.
- **`EARLY_ACCESS_SEATS_BASELINE` is added to that count** (`_effective_claimed`)
  and defaults to 0. It covers real places gone that never became rows —
  testers recruited before the page went up, invites handed out in person — so
  the public meter is not wrong by however many of those exist. It is **not** a
  dial for making the beta look busier: the number is stated to visitors as a
  fact about how many seats are taken. The waitlist threshold is measured after
  it is applied, or an already-full beta would keep handing out seats it does
  not have, and `seats_claimed` is clamped to the cap so a full meter reads
  "100 of 100" rather than overflowing.
- **PII is NOT masked into the Sheet**, deliberately — unlike the signup ping
  and the issue register. The team has to *phone* these people and a masked
  number cannot be dialled. The cost: rows sit outside the database, so an
  erasure request cannot reach them automatically. Delete a row once that
  person is onboarded (their record then lives in the DB, where erasure works)
  or has declined. The Slack ping *is* masked — a channel is read by more
  people and retained longer than a row the team prunes.
- **Two escape hatches from the signup block, both config, neither needing a
  deploy** (`auth_router._can_sign_up`): `SIGNUPS_OPEN=true` reopens it for
  everyone; `EARLY_ACCESS_ALLOWED_PHONES` lets named numbers through while it
  stays shut. The allowlist is **empty by default**, which is the launch state
  — and is how an approved applicant is actually let in.
- **The block is signup-only.** `/login`, `/token` and the PIN-reset flow are
  untouched: they all require an existing account, and locking out the current
  users is never what "close signups" means. `family_router` can still create a
  user row for an invited household member — that is an invite by an existing
  user, not a public signup, and `/family` is served by `ComingSoon` anyway.
- **`MobileStatusResponse.can_sign_up` is advisory.** `/auth/check-mobile`
  reports the rule so the entry screen can redirect before asking for details,
  but `/auth/signup` enforces it server-side; a client that ignores the flag
  gets a 403, not an account. It is always `true` for a number that already
  exists — that person is signing *in*.
- **`_SIGNUPS_CLOSED_DETAIL` is rendered verbatim by the frontend.** The
  wording is part of the contract; change it in both repos or not at all.
- **The honeypot answers 201.** Telling a scraper which field gave it away is
  free information. Nothing is written, and the seat numbers in that reply are
  pinned at the cap so it leaks nothing about the real state of the list.

## The Google Apps Script

Paste the WHOLE script, constants included — `TOKEN`, `SHEET` and `HEADERS`
are referenced by every function below, so pasting from `function sheet_()`
down throws `ReferenceError: SHEET is not defined` on the first request.

Bind this to the team's Sheet (Extensions → Apps Script), then **Deploy → New
deployment → Web app**, "Execute as: Me", "Who has access: Anyone". Paste the
`/exec` URL into `EARLY_ACCESS_SHEET_WEBHOOK_URL` and set a matching
`EARLY_ACCESS_SHEET_TOKEN` here and in `TOKEN` below.

```javascript
const TOKEN = 'put-the-same-value-as-EARLY_ACCESS_SHEET_TOKEN-here';
const SHEET = 'Entries';
const HEADERS = ['Date', 'Name', 'Email', 'WhatsApp', 'Profession', 'Source', 'Seat', 'Notes'];

function sheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(SHEET);
  if (!sh) {
    sh = ss.insertSheet(SHEET);
    sh.appendRow(HEADERS);
    sh.setFrozenRows(1);
  }
  return sh;
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

// Rows minus the header. Blank trailing rows are not counted.
function claimed_(sh) {
  return Math.max(0, sh.getLastRow() - 1);
}

function doGet(e) {
  if ((e.parameter.token || '') !== TOKEN) return json_({ ok: false, error: 'forbidden' });
  return json_({ ok: true, claimed: claimed_(sheet_()) });
}

function doPost(e) {
  const body = JSON.parse(e.postData.contents || '{}');
  if ((body.token || '') !== TOKEN) return json_({ ok: false, error: 'forbidden' });

  // One writer at a time: two people submitting at once must not take the same
  // seat number, and must not both append after reading the same last row.
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const sh = sheet_();
    const email = String(body.email || '').trim().toLowerCase();

    // De-duplicate on email: a repeat submission keeps the seat it already has.
    const n = claimed_(sh);
    if (n > 0) {
      const emails = sh.getRange(2, 3, n, 1).getValues();
      for (let i = 0; i < emails.length; i++) {
        if (String(emails[i][0]).trim().toLowerCase() === email) {
          return json_({ ok: true, seat: i + 1, claimed: n, already_registered: true });
        }
      }
    }

    const seat = n + 1;
    sh.appendRow([
      body.date || '', body.name || '', email, body.whatsapp || '',
      body.profession || '', body.source || '', seat, '',
    ]);
    return json_({ ok: true, seat: seat, claimed: seat, already_registered: false });
  } finally {
    lock.releaseLock();
  }
}
```

Re-deploy as a **new version** after any edit — Apps Script serves the last
deployed version, not the saved one, which is the usual reason a change appears
to do nothing.

## Testing

`tests/test_early_access.py` covers the register contract end-to-end against a
stubbed Apps Script (dedupe, waitlist, honeypot, rate limit, 503) plus the
signup block. Per the repo convention these test dirs are gitignored — a new
file needs `git add -f`.

## Don't read

- `__pycache__/` — build cache.
