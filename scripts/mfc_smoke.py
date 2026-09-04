"""DEV-ONLY — walk the whole MF Central integration, both directions.

Signs up a throwaway investor called **Testing XYZ**, imports a statement over
the CAS consent flow, then places one order of every FT family and drives each
through OTP, consent and settlement.

    python scripts/mfc_smoke.py                 # both halves
    python scripts/mfc_smoke.py --only cas
    python scripts/mfc_smoke.py --only ft
    python scripts/mfc_smoke.py --api http://127.0.0.1:8000/api/v1

Requires a backend that is already running with the mock mounted (the default
when no MFC_CLIENT_ID is configured) — start it with
``uvicorn main:app --reload`` first. It talks HTTP only: no imports from
``app``, so it exercises the same surface a browser does, including auth,
serialisation and the error mapping.

Every test account is named "Testing XYZ" so that rows this script leaves in the
dev database are identifiable at a glance and never mistaken for a real
investor's. The name is checked, not merely passed — see :func:`_signup`.
"""

from __future__ import annotations

import argparse
import random
import re
import sys
from typing import Any, Optional

import httpx

# The name every account this script creates must carry. First name is checked
# exactly: a row called anything else in the dev DB did not come from here.
TEST_FIRST_NAME = "Testing"
TEST_LAST_NAME = "XYZ"
TEST_FULL_NAME = f"{TEST_FIRST_NAME} {TEST_LAST_NAME}"

# A PAN from MF Central's published UAT list, and the OTP their UAT rule derives
# from it: "00" + the last four digits.
UAT_PAN = "AATPJ9485B"
UAT_OTP = "00" + UAT_PAN[5:9]

DEFAULT_API = "http://127.0.0.1:8000/api/v1"

_ok = 0
_bad = 0


def check(condition: bool, label: str, detail: str = "") -> bool:
    global _ok, _bad
    if condition:
        _ok += 1
        print(f"  [ ok ] {label}{(' — ' + detail) if detail else ''}")
    else:
        _bad += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")
    return condition


def head(text: str) -> None:
    print(f"\n{'=' * 66}\n{text}\n{'=' * 66}")


def _signup(client: httpx.Client, api: str) -> dict[str, str]:
    """Create a throwaway investor named Testing XYZ and return auth headers."""
    suffix = random.randint(100_000_000, 999_999_999)
    body = {
        "country_code": "+91",
        "mobile": f"9{suffix}",
        "password": "Test@12345",
        "email": f"testing.xyz.{suffix}@example.com",
        "first_name": TEST_FIRST_NAME,
        "last_name": TEST_LAST_NAME,
    }
    r = client.post(f"{api}/auth/signup", json=body)
    if r.status_code >= 400:
        raise SystemExit(f"signup failed ({r.status_code}): {r.text[:300]}")
    token = r.json().get("access_token")
    if not token:
        r = client.post(
            f"{api}/auth/login",
            json={"mobile": body["mobile"], "password": body["password"]},
        )
        token = r.json().get("access_token")
    if not token:
        raise SystemExit("no access token returned")
    headers = {"Authorization": f"Bearer {token}"}

    me = client.get(f"{api}/auth/me", headers=headers).json()
    # Asserted rather than assumed: the whole point of the convention is that a
    # row in the dev DB can be recognised, and a silently-dropped name would
    # defeat that without failing anything.
    check(
        me.get("first_name") == TEST_FIRST_NAME,
        f'account first name is exactly "{TEST_FIRST_NAME}"',
        f"got {me.get('first_name')!r}",
    )
    check(
        f"{me.get('first_name')} {me.get('last_name')}" == TEST_FULL_NAME,
        f'account full name is "{TEST_FULL_NAME}"',
    )
    print(f"  signed up {body['mobile']} as {TEST_FULL_NAME}")
    return headers


# --------------------------------------------------------------------------- CAS


def run_cas(client: httpx.Client, api: str, headers: dict[str, str]) -> None:
    head("CAS — reading holdings OUT of the registrars")

    cfg = client.get(f"{api}/mfc-cas/config", headers=headers).json()
    check(cfg.get("enabled") is True, "MF Central is enabled", cfg.get("environment", ""))

    r = client.post(f"{api}/mfc-cas/start", headers=headers, json={"pan_no": UAT_PAN})
    if not check(r.status_code == 200, "POST /mfc-cas/start", r.text[:120]):
        return
    start = r.json()
    print(f"         reqId {start['req_id']} · OTP to {start['otp_destination']}")

    page = client.get(start["redirect_url"])
    check(page.status_code == 200, "consent page loads")
    check(
        "Could not decrypt" not in page.text,
        "consent page decrypted our redirect payload",
        "proves the URL cipher (raw key, random IV) is right",
    )
    check(start["req_id"] in page.text, "consent page shows our reqId")

    qrs = dict(
        re.findall(
            r'href="data:image/png;base64,([^"]+)" download="mfc-cas-(\w+)\.png',
            page.text,
        )
    )
    qrs = {v: k for k, v in qrs.items()}
    if not check("detailed" in qrs and "summary" in qrs, "both QR codes present"):
        return

    # Summary first: it must be readable but refused, with the data still shown.
    r = client.post(
        f"{api}/mfc-cas/validate-qr",
        headers=headers,
        json={"qr_code": qrs["summary"], "request_id": start["request_id"]},
    )
    summary = r.json() if r.status_code == 200 else {}
    check(r.status_code == 200, "summary QR is accepted as a request")
    check(summary.get("ingest") is None, "summary statement is NOT imported")
    check(bool(summary.get("rejection")), "…and says why", (summary.get("rejection") or "")[:60])
    check(
        bool((summary.get("data") or {}).get("positions")),
        "…while still showing what the investor consented to",
    )

    r = client.post(
        f"{api}/mfc-cas/validate-qr",
        headers=headers,
        json={"qr_code": qrs["detailed"], "request_id": start["request_id"]},
    )
    if not check(r.status_code == 200, "detailed QR exchanges for a statement", r.text[:120]):
        return
    result = r.json()
    ingest = result.get("ingest") or {}
    check(bool(ingest), "detailed statement IS imported")
    check(ingest.get("schemes", 0) > 0, "schemes imported", str(ingest.get("schemes")))
    check(
        ingest.get("mf_transactions_inserted", 0) > 0,
        "transactions imported",
        str(ingest.get("mf_transactions_inserted")),
    )
    check(ingest.get("normalize_error") is None, "no normalisation error")

    data = result.get("data") or {}
    investor = data.get("investor") or {}
    check(
        (investor.get("name") or "").startswith(TEST_FIRST_NAME),
        f'statement investor name starts with "{TEST_FIRST_NAME}"',
        str(investor.get("name")),
    )
    kinds = {t.get("kind") for t in data.get("transactions") or []}
    check("UNKNOWN" in kinds, "non-financial rows are classified and dropped")
    check(
        {"PURCHASE", "PURCHASE_SIP", "REDEMPTION", "SWITCH_IN", "SWITCH_OUT"} <= kinds,
        "every unit-moving transaction type is recognised",
    )

    pf = client.get(f"{api}/portfolio/", headers=headers)
    check(pf.status_code == 200, "portfolio rebuilt from the statement")
    if pf.status_code == 200:
        print(f"         portfolio value {pf.json().get('total_value')}")


# --------------------------------------------------------------------------- FT

FT_CASES: list[tuple[str, dict[str, Any], bool]] = [
    (
        "purchase (lumpsum)",
        {
            "kind": "purchase",
            "isin": "INF179K01608",
            "amount": 25000,
            "scheme_name": "HDFC Flexi Cap Fund - Regular Plan - Growth",
            "folio": "17064219",
            "bank": {
                "account_no": "12221150010158",
                "account_type": "SB",
                "name": "HDFC Bank Ltd",
                "ifsc": "HDFC0001222",
            },
        },
        True,
    ),
    (
        "purchase (SIP, monthly)",
        {
            "kind": "purchase",
            "isin": "INF179K01608",
            "amount": 5000,
            "amc": "H",
            "frequency": "Monthly",
            "start_date": "10-Oct-2026",
            "installments": 60,
        },
        False,
    ),
    (
        "additional purchase",
        {"kind": "additional", "isin": "INF179K01608", "amount": 10000, "amc": "H",
         "folio": "17064219"},
        True,
    ),
    (
        "redeem (units)",
        {"kind": "redeem", "isin": "INF179K01442", "units": 100, "amc": "H",
         "folio": "17064219"},
        False,
    ),
    (
        "switch",
        {"kind": "switch", "isin": "INF179K01442", "to_isin": "INF179K01608",
         "amount": 10000, "amc": "H", "folio": "17064219", "dist_id": "ARN-48944"},
        False,
    ),
    (
        "STP",
        {"kind": "stp", "isin": "INF179K01442", "to_isin": "INF179K01608",
         "amount": 2000, "amc": "H", "folio": "17064219", "frequency": "Monthly",
         "start_date": "01-Nov-2026"},
        False,
    ),
    (
        "SWP",
        {"kind": "swp", "isin": "INF179K01442", "amount": 3000, "amc": "H",
         "folio": "17064219", "frequency": "Quarterly", "start_date": "05-Dec-2026"},
        False,
    ),
    (
        "SIP pause",
        {"kind": "sip_pause", "isin": "INF179KC1HO1", "folio": "33557599",
         "amc": "H", "user_trxn_no": "37822295", "pause_installments": 3,
         "reason": "Cash flow", "reason_code": "13"},
        False,
    ),
    (
        "SIP cancel",
        {"kind": "sip_cancel", "isin": "INF179KC1HO1", "folio": "33557599",
         "amc": "H", "user_trxn_no": "37822295", "reason": "No longer needed"},
        False,
    ),
]

# Payloads our own validators must refuse before anything reaches MF Central —
# their rejections arrive after the order exists on their side.
FT_GUARDS: list[tuple[str, dict[str, Any]]] = [
    ("switch with no destination", {"kind": "switch", "isin": "INF179K01442",
                                    "amount": 100, "amc": "H"}),
    ("redeem with nothing to redeem", {"kind": "redeem", "isin": "INF179K01442",
                                       "amc": "H"}),
    ("STP with an unknown cadence", {"kind": "stp", "isin": "INF179K01442",
                                     "to_isin": "INF179K01608", "amount": 100,
                                     "amc": "H", "frequency": "fortnight-ish",
                                     "start_date": "01-Nov-2026"}),
    ("an AMC we cannot resolve", {"kind": "redeem", "isin": "INF179K01442",
                                  "units": 1, "amc": "Not A Real Fund House"}),
    ("SIP pause with no registrar reference", {"kind": "sip_pause",
                                               "isin": "INF179KC1HO1", "amc": "H"}),
]


def run_ft(client: httpx.Client, api: str, headers: dict[str, str]) -> None:
    head("FT — writing orders BACK to the registrars")

    m = client.get(f"{api}/mfc-ft/masters", headers=headers)
    if not check(m.status_code == 200, "GET /mfc-ft/masters"):
        return
    masters = m.json()
    check(len(masters["amcs"]) == 43, "43 fund houses in the AMC master")
    check(len(masters["frequencies"]) == 17, "17 cadences in the frequency master")
    check(len(masters["transaction_kinds"]) == 9 - 1, "8 transaction families")

    identity = {"pan_no": UAT_PAN, "mobile": "9876543210"}

    print("\n-- SIP pause pre-flight")
    r = client.post(
        f"{api}/mfc-ft/pause-cancel/validate",
        headers=headers,
        json={"kind": "sip_pause", "isin": "INF179KC1HO1", "folio": "33557599",
              "amc": "H", "user_trxn_no": "37822295", **identity},
    )
    check(r.status_code == 200 and r.json().get("ok") is True,
          "a real running SIP validates", r.json().get("message", "")[:60])

    for label, body, pays in FT_CASES:
        print(f"\n-- {label}")
        r = client.post(f"{api}/mfc-ft/orders", headers=headers,
                        json={**body, **identity})
        if not check(r.status_code == 200, "placed", r.text[:110]):
            continue
        res = r.json()
        order = res["order"]
        oid = order["id"]
        print(f"         amc={order['amc']} ({order['amc_name']}) ref={order['req_id']}")

        r = client.post(f"{api}/mfc-ft/orders/{oid}/otp", headers=headers)
        check(r.status_code == 200, "OTP sent", r.json().get("message", "")[:60])

        r = client.post(f"{api}/mfc-ft/orders/{oid}/consent", headers=headers,
                        json={"otp": "000000"})
        check(r.status_code == 400, "a wrong OTP is refused and not fatal")

        r = client.post(f"{api}/mfc-ft/orders/{oid}/consent", headers=headers,
                        json={"otp": UAT_OTP})
        check(r.status_code == 200 and r.json()["order"]["status"] == "consented",
              "the right OTP is accepted (MFC answers 202 with an empty body)")

        if pays:
            r = client.post(f"{api}/mfc-ft/orders/{oid}/payment", headers=headers,
                            json={"status": "SUCCESS", "umrn": "YESB7010408220000010"})
            check(r.status_code == 200, "payment confirmed")

        first = client.get(f"{api}/mfc-ft/orders/{oid}/status", headers=headers).json()
        check(first["order"]["status"] == "processing",
              "first poll reports the registrar still working",
              first["order"]["rta_status"] or "")
        final = client.get(f"{api}/mfc-ft/orders/{oid}/status", headers=headers).json()
        check(final["order"]["status"] == "success", "settles on a later poll",
              final["order"]["rta_status"] or "")
        check(bool(final["order"]["user_trxn_no"]),
              "registrar transaction number recorded",
              final["order"]["user_trxn_no"] or "")

    print("\n-- guards (must be refused BEFORE MF Central sees them)")
    for label, body in FT_GUARDS:
        r = client.post(f"{api}/mfc-ft/orders", headers=headers,
                        json={**body, **identity})
        check(r.status_code == 400, label, str(r.json().get("detail"))[:70])

    print("\n-- a rejection carried inside an HTTP 200")
    r = client.post(
        f"{api}/mfc-ft/orders",
        headers=headers,
        json={"kind": "redeem", "isin": "INF179K01442", "amount": 5013, "amc": "H",
              "folio": "17064219", **identity},
    )
    check(r.status_code == 400,
          "a 200 carrying per-scheme errors is reported as a rejection",
          str(r.json().get("detail"))[:70])

    book = client.get(f"{api}/mfc-ft/orders", headers=headers).json()["orders"]
    check(len(book) >= len(FT_CASES), f"order book holds {len(book)} orders")


# --------------------------------------------------------------------------- main


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--only", choices=("cas", "ft"), default=None)
    args = parser.parse_args(argv)

    print(f"MF Central smoke — {args.api}")
    print(f"investor: {TEST_FULL_NAME} · PAN {UAT_PAN} · UAT OTP {UAT_OTP}")

    with httpx.Client(timeout=180) as client:
        try:
            client.get(f"{args.api}/mfc-ft/masters")
        except httpx.HTTPError as exc:
            raise SystemExit(
                f"backend unreachable at {args.api} ({exc}).\n"
                "Start it first: uvicorn main:app --reload"
            ) from exc

        headers = _signup(client, args.api)
        if args.only != "ft":
            run_cas(client, args.api, headers)
        if args.only != "cas":
            run_ft(client, args.api, headers)

    print(f"\n{'=' * 66}")
    print(f"{_ok} passed, {_bad} failed")
    return 1 if _bad else 0


if __name__ == "__main__":
    sys.exit(main())
