"""Adapter — MF Central `validateQRCode` payload → legacy parsed-CAS dict.

The whole CAMS ingest pipeline (``cams_cas_ingest``) consumes one dict shape,
originally produced by the in-process ``casparser`` library and since then by
``casparser_adapter``. This module adds MF Central as a third producer of that
same shape, so a statement fetched over MFC's consent flow lands through exactly
the code path a PDF upload does: transaction-derived holdings, the
statement-balance guard, the SUMMARY / zero-value rejections, the audit rows.

MFC returns one of two documents, decided by the investor inside MFC's own UI,
not by us:

* **Detailed** — ``data[].dtTransaction[]`` (the ledger) + ``data[].dtSummary[]``
  (per folio+scheme position) + ``investorDetails``. This is the one that can
  rebuild a portfolio.
* **Summary** — ``data[].schemes[]`` (positions only, but far richer per row:
  ISIN, bank mandate, transactability flags) + ``data[].summary[]`` per AMC +
  ``portfolio[]`` split demat / non-demat. No ledger.

Both are mapped; the Summary one comes out tagged ``cas_type="SUMMARY"`` and is
rejected downstream with the "please pick Detailed" message, which is the honest
outcome — a Summary statement genuinely cannot produce transaction history.

Two structural differences from the PDF path are worth knowing:

* MFC is **flat**. Folios are not a nesting level; every ``dtSummary`` /
  ``dtTransaction`` row repeats its own ``folio``, so the folio grouping here is
  reconstructed, not read.
* MFC has **no transaction type enum**. The PDF parser emits PURCHASE /
  REDEMPTION / SWITCH_IN…; MFC gives free-text ``trxnDesc`` plus the sign of
  ``trxnUnits``. :func:`classify_transaction` rebuilds the enum, and anything it
  cannot place comes back ``UNKNOWN`` — which the pipeline already skips, so an
  unrecognised description drops a row rather than corrupting a balance.

Pure transform: no I/O.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from datetime import datetime
from typing import Any, Iterable, Optional

# Reuse the PDF path's error type so the ingest service has one thing to catch.
from app.domains.ingestion.services.casparser_adapter import CasResponseShapeError


def _s(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _f(value: object, default: float = 0.0) -> float:
    """MFC quotes numbers as strings about half the time, and blanks as ``""``."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def _date(value: object) -> Optional[str]:
    """Normalise MFC's mixed date casing to ``DD-Mon-YYYY``.

    MFC emits ``24-SEP-2020`` in the detailed ledger and ``01-Jan-2026`` in the
    summary. ``mf_aa_normalizer._parse_date`` accepts both, but the audit rows
    are stored as text and read by humans, so they are worth making uniform.
    """
    text = _s(value)
    if not text:
        return None
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%d-%b-%Y")
        except ValueError:
            continue
    return text


# --------------------------------------------------------------------------- asset type

# MFC's assetType vocabulary, mapped onto the tokens `resolve_asset_bucket`
# understands. Anything else becomes "N/A", which tells the classifier to fall
# back to the scheme name — the right answer for MFC's blanks and one-offs.
_ASSET_TYPE_MAP = {
    "EQUITY": "EQUITY",
    "DEBT": "DEBT",
    "HYBRID": "HYBRID",
    "LIQUID": "DEBT",
    "ELSS": "EQUITY",
    "GOLD": "OTHER",
    "OTHER": "OTHER",
    "OTHERS": "OTHER",
}


def _map_asset_type(value: object) -> str:
    return _ASSET_TYPE_MAP.get((_s(value) or "").upper(), "N/A")


# --------------------------------------------------------------------------- txn types

# Ordered longest-intent-first: "SWITCH OUT" must be tested before "SWITCH", and
# the SIP marker before the generic purchase, or every SIP instalment lands as a
# plain PURCHASE and the SIP-detection downstream sees nothing.
_DESC_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"SWITCH[\s_-]*OUT|SWITCH\s*FROM|STP\s*OUT|TRANSFER\s*OUT"),
        "SWITCH_OUT",
    ),
    (re.compile(r"SWITCH[\s_-]*IN|SWITCH\s*TO|STP\s*IN|TRANSFER\s*IN"), "SWITCH_IN"),
    (re.compile(r"MERGER|AMALGAMAT"), "SWITCH_IN_MERGER"),
    (
        re.compile(r"(DIVIDEND|IDCW).*(REINVEST|RE-INVEST)|REINVEST"),
        "DIVIDEND_REINVEST",
    ),
    (re.compile(r"(DIVIDEND|IDCW).*(PAYOUT|PAID)"), "DIVIDEND_PAYOUT"),
    (re.compile(r"\bSIP\b|SYSTEMATIC\s+INVEST"), "PURCHASE_SIP"),
    (re.compile(r"\bSWP\b|SYSTEMATIC\s+WITHDRAW"), "REDEMPTION"),
    (re.compile(r"REDEMPTION|REDEEM|REPURCHASE"), "REDEMPTION"),
    (re.compile(r"PURCHASE|SUBSCRIPTION|INVESTMENT|ALLOT"), "PURCHASE"),
    (re.compile(r"STAMP\s*DUTY"), "STAMP_DUTY_TAX"),
    (re.compile(r"\bSTT\b|SECURITIES\s+TRANSACTION\s+TAX"), "STT_TAX"),
    (re.compile(r"\bTDS\b"), "TDS_TAX"),
    (re.compile(r"SEGREGAT"), "SEGREGATION"),
    (re.compile(r"REVERS"), "REVERSAL"),
)

# MFC's own trxnTypeFlag, when populated. Their CAS samples ship it empty, so
# this is a bonus signal rather than the primary one — but when present it is
# authoritative, because it is the RTA's own classification.
_FLAG_MAP = {
    "P": "PURCHASE",
    "PS": "PURCHASE_SIP",
    "SIP": "PURCHASE_SIP",
    "R": "REDEMPTION",
    "SI": "SWITCH_IN",
    "SO": "SWITCH_OUT",
    "DR": "DIVIDEND_REINVEST",
    "DP": "DIVIDEND_PAYOUT",
}


def classify_transaction(
    description: Optional[str], units: float, flag: Optional[str] = None
) -> str:
    """Rebuild the casparser transaction enum from MFC's free text.

    The unit sign is the tie-breaker, not the primary signal, because MFC states
    redemption units as positive on some AMCs and negative on others: a
    description that clearly says "Redemption" outranks a positive unit count.
    Rows that move no units at all (address updates, KYC flags, nominee changes
    — a real and sizeable share of any MFC ledger) resolve to UNKNOWN and are
    dropped by the caller.
    """
    mapped = _FLAG_MAP.get((flag or "").strip().upper())
    if mapped:
        return mapped

    text = (description or "").upper()
    for pattern, kind in _DESC_RULES:
        if pattern.search(text):
            return kind

    if units > 0:
        return "PURCHASE"
    if units < 0:
        return "REDEMPTION"
    return "UNKNOWN"


# --------------------------------------------------------------------------- grouping


def _scheme_key(row: dict[str, Any]) -> tuple[str, str]:
    """Identity of one position: folio + ISIN, falling back to the scheme code.

    ISIN first because MFC's ``scheme`` field is an AMC-local code ("02", "EQGP")
    that collides across AMCs; folio is included because the same fund held in
    two folios is two positions with two cost bases.
    """
    folio = (_s(row.get("folio")) or "").upper()
    ident = (
        _s(row.get("isin")) or _s(row.get("scheme")) or _s(row.get("schemeName")) or ""
    ).upper()
    return folio, ident


def _folio_key(row: dict[str, Any]) -> tuple[str, str]:
    return (
        (_s(row.get("folio")) or "").upper(),
        (_s(row.get("amc")) or _s(row.get("amcName")) or "").upper(),
    )


def _iter_blocks(payload: dict[str, Any], key: str) -> Iterable[dict[str, Any]]:
    """Flatten ``data[].<key>[]`` — MFC nests one level for no stated reason and
    has been observed returning several ``data`` entries for multi-RTA users."""
    for block in payload.get("data") or []:
        if not isinstance(block, dict):
            continue
        for row in block.get(key) or []:
            if isinstance(row, dict):
                yield row


# --------------------------------------------------------------------------- detailed


def _map_detailed(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """``dtSummary`` + ``dtTransaction`` → legacy ``folios[]``."""
    ledger: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in _iter_blocks(payload, "dtTransaction"):
        units = _f(row.get("trxnUnits"))
        kind = classify_transaction(
            _s(row.get("trxnDesc")), units, _s(row.get("trxnTypeFlag"))
        )
        ledger.setdefault(_scheme_key(row), []).append(
            {
                "date": _date(row.get("trxnDate") or row.get("postedDate")),
                "description": _s(row.get("trxnDesc")),
                "amount": _f(row.get("trxnAmount")),
                "units": units,
                "nav": _f(row.get("purchasePrice")) or None,
                "balance": None,
                "type": kind,
                "dividend_rate": None,
                "stamp_duty": _f(row.get("stampDuty")) or None,
                "stt": _f(row.get("sttTax")) or None,
            }
        )

    # Sort each ledger oldest-first: `_populate_children` reads the LAST entry as
    # the scheme's last transaction date, and `_derive_scheme_snapshot` walks the
    # list in order to build the running position.
    for rows in ledger.values():
        rows.sort(key=lambda t: _sort_date(t.get("date")))

    folios: "OrderedDict[tuple[str, str], dict[str, Any]]" = OrderedDict()
    seen_schemes: set[tuple[str, str]] = set()

    for row in _iter_blocks(payload, "dtSummary"):
        key = _scheme_key(row)
        if key in seen_schemes:
            continue
        seen_schemes.add(key)
        folio = folios.setdefault(
            _folio_key(row),
            {
                "folio": _s(row.get("folio")),
                "amc": _s(row.get("amcName")) or _s(row.get("amc")),
                "PAN": _s(payload.get("pan")),
                "registrar": _s(row.get("rtaCode")),
                "schemes": [],
            },
        )
        folio["schemes"].append(
            {
                "scheme": _s(row.get("schemeName")),
                "isin": _s(row.get("isin")),
                "amfi": None,
                "type": _map_asset_type(row.get("assetType")),
                "rta_code": _s(row.get("rtaCode")),
                "advisor": _s(row.get("brokerCode")),
                "open": _f(row.get("openingBal")) or None,
                "close": _f(row.get("closingBalance")),
                "valuation": {
                    "value": _f(row.get("marketValue")),
                    "cost": _f(row.get("costValue")) or None,
                    "nav": _f(row.get("nav")) or None,
                    "date": _date(row.get("lastNavDate")),
                },
                "transactions": ledger.get(key, []),
            }
        )

    # A ledger entry whose position is absent from dtSummary is a fully exited
    # holding. It carries no valuation, but its transactions still belong in the
    # history — dropping them would make a sold-out fund look like it never
    # existed and would silently change realised-gain answers.
    for key, rows in ledger.items():
        if key in seen_schemes or not rows:
            continue
        sample = _first_ledger_row(payload, key)
        if sample is None:
            continue
        folio = folios.setdefault(
            _folio_key(sample),
            {
                "folio": _s(sample.get("folio")),
                "amc": _s(sample.get("amcName")) or _s(sample.get("amc")),
                "PAN": _s(payload.get("pan")),
                "registrar": None,
                "schemes": [],
            },
        )
        folio["schemes"].append(
            {
                "scheme": _s(sample.get("schemeName")),
                "isin": _s(sample.get("isin")),
                "amfi": None,
                "type": "N/A",
                "rta_code": None,
                "advisor": None,
                "open": None,
                "close": 0.0,
                "valuation": {"value": 0.0, "cost": None, "nav": None, "date": None},
                "transactions": rows,
            }
        )

    return list(folios.values())


def _first_ledger_row(
    payload: dict[str, Any], key: tuple[str, str]
) -> Optional[dict[str, Any]]:
    for row in _iter_blocks(payload, "dtTransaction"):
        if _scheme_key(row) == key:
            return row
    return None


def _sort_date(value: Optional[str]) -> tuple[int, int, int]:
    """Sort key that keeps unparseable dates at the front rather than crashing."""
    if not value:
        return (0, 0, 0)
    try:
        parsed = datetime.strptime(value, "%d-%b-%Y")
    except ValueError:
        return (0, 0, 0)
    return (parsed.year, parsed.month, parsed.day)


# --------------------------------------------------------------------------- summary


def _map_summary(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """``schemes[]`` → legacy ``folios[]``, with no transactions.

    Deliberately produces a ledger-less document so the caller tags it SUMMARY
    and the pipeline rejects it. Mapping it at all is what lets the frontend
    show the investor exactly what they consented to before telling them it is
    the wrong variant.
    """
    folios: "OrderedDict[tuple[str, str], dict[str, Any]]" = OrderedDict()
    for row in _iter_blocks(payload, "schemes"):
        folio = folios.setdefault(
            _folio_key(row),
            {
                "folio": _s(row.get("folio")),
                "amc": _s(row.get("amcName")) or _s(row.get("amc")),
                "PAN": _s(payload.get("pan")),
                "registrar": _s(row.get("rtaName")),
                "schemes": [],
            },
        )
        folio["schemes"].append(
            {
                "scheme": _s(row.get("schemeName")),
                "isin": _s(row.get("isin")),
                "amfi": None,
                "type": _map_asset_type(row.get("assetType")),
                "rta_code": _s(row.get("rtaName")),
                "advisor": _s(row.get("brokerCode")),
                "open": None,
                "close": _f(row.get("closingBalance") or row.get("availableUnits")),
                "valuation": {
                    "value": _f(row.get("currentMktValue")),
                    "cost": _f(row.get("costValue")) or None,
                    "nav": _f(row.get("nav")) or None,
                    "date": _date(row.get("navDate")),
                },
                "transactions": [],
            }
        )
    return list(folios.values())


# --------------------------------------------------------------------------- investor


def _investor_info(payload: dict[str, Any]) -> dict[str, Any]:
    """Investor identity, assembled from wherever MFC put it this time.

    The detailed variant fills ``investorDetails``; the summary variant leaves it
    ``{}`` and repeats the name on every scheme row instead. Both are read, and
    the top-level ``pan``/``email``/``mobile`` fill the remaining gaps.
    """
    details = payload.get("investorDetails") or {}
    if not isinstance(details, dict):
        details = {}

    name = " ".join(
        part
        for part in (
            _s(details.get("investorFirstName")),
            _s(details.get("investorMiddleName")),
            _s(details.get("investorLastName")),
        )
        if part
    ).strip()
    if not name:
        for row in _iter_blocks(payload, "schemes"):
            name = _s(row.get("investorName")) or ""
            if name:
                break

    address = details.get("address") or {}
    if not isinstance(address, dict):
        address = {}
    address_line = ", ".join(
        part
        for part in (
            _s(address.get("address1")),
            _s(address.get("address2")),
            _s(address.get("address3")),
            _s(address.get("city")),
            _s(address.get("state")),
            _s(address.get("pincode")),
        )
        if part
    )

    return {
        "name": name or None,
        "email": _s(details.get("email")) or _s(payload.get("email")),
        "mobile": _s(details.get("mobile")) or _s(payload.get("mobile")),
        "address": address_line or None,
        "pan": _s(payload.get("pan")),
    }


# --------------------------------------------------------------------------- entry point


def to_legacy_parsed(payload: dict[str, Any]) -> dict[str, Any]:
    """Map an MFC ``validateQRCode`` payload onto the legacy parsed-CAS dict.

    Raises :class:`CasResponseShapeError` when the payload carries neither a
    ledger nor a position list — MFC's shape for "the investor consented but has
    no folios with this PAN", which is a real outcome and not an error we should
    surface as a crash.
    """
    error_message = _s(payload.get("errorMessage"))

    folios = _map_detailed(payload)
    has_transactions = any(
        scheme["transactions"] for folio in folios for scheme in folio["schemes"]
    )
    if not folios:
        folios = _map_summary(payload)

    if not folios:
        raise CasResponseShapeError(
            error_message
            or "MF Central returned no mutual-fund folios for this PAN. "
            "If you hold funds under a different PAN or PEKRN, use that one."
        )

    return {
        "cas_type": "DETAILED" if has_transactions else "SUMMARY",
        "file_type": "MFCENTRAL",
        "statement_period": {
            "from": _date(payload.get("fromDate")),
            "to": _date(payload.get("toDate")),
        },
        "investor_info": _investor_info(payload),
        "folios": folios,
    }


# --------------------------------------------------------------------------- display


def summarize_for_display(payload: dict[str, Any]) -> dict[str, Any]:
    """Everything MFC sent, flattened for the import screen.

    The ingest pipeline keeps only what it can act on — units, amounts, dates,
    ISINs. MFC also returns the bank mandate, the transactability flags, the KYC
    and nominee status, and the demat split, none of which has a home in our
    schema yet and all of which is the reason to prefer this source. This is
    what the frontend renders so that value is at least visible while it is
    decided where it should live.
    """
    schemes = list(_iter_blocks(payload, "schemes"))
    dt_summary = list(_iter_blocks(payload, "dtSummary"))
    dt_transactions = list(_iter_blocks(payload, "dtTransaction"))

    positions: list[dict[str, Any]] = []
    for row in schemes:
        bank = row.get("bank") or {}
        positions.append(
            {
                "amc_name": _s(row.get("amcName")),
                "folio": _s(row.get("folio")),
                "scheme_name": _s(row.get("schemeName")),
                "scheme_option": _s(row.get("schemeOption")),
                "isin": _s(row.get("isin")),
                "asset_type": _s(row.get("assetType")),
                "scheme_type": _s(row.get("schemeType")),
                "plan_mode": _s(row.get("planMode")),
                "nav": _f(row.get("nav")) or None,
                "nav_date": _date(row.get("navDate")),
                "units": _f(row.get("closingBalance") or row.get("availableUnits")),
                "lien_eligible_units": _f(row.get("lienEligibleUnits")) or None,
                "market_value": _f(row.get("currentMktValue")),
                "cost_value": _f(row.get("costValue")) or None,
                "gain_loss": _f(row.get("gainLoss")) or None,
                "gain_loss_pct": _f(row.get("gainLossPercentage")) or None,
                "is_demat": _s(row.get("isDemat")),
                "rta_name": _s(row.get("rtaName")),
                "broker_code": _s(row.get("brokerCode")),
                "broker_name": _s(row.get("brokerName")),
                "kyc_status": _s(row.get("kycStatus")),
                "nominee_status": _s(row.get("nomineeStatus")),
                "tax_status": _s(row.get("taxStatus")),
                "mode_of_holding": _s(row.get("modeOfHolding")),
                # The transactability flags: whether this exact folio+scheme can
                # be bought, redeemed, switched or put on a SIP/STP/SWP. Nothing
                # in the PDF path carries these, and a rebalancing plan that
                # ignores them proposes trades the RTA will reject.
                "allows": {
                    "purchase": _s(row.get("purAllow")) == "Y",
                    "redeem": _s(row.get("redAllow")) == "Y",
                    "switch": _s(row.get("swtAllow")) == "Y",
                    "sip": _s(row.get("sipAllow")) == "Y",
                    "stp": _s(row.get("stpAllow")) == "Y",
                    "swp": _s(row.get("swpAllow")) == "Y",
                },
                "bank": (
                    {
                        "name": _s(bank.get("name")),
                        "account_no": _mask_account(_s(bank.get("accountNo"))),
                        "account_type": _s(bank.get("accountType")),
                        "ifsc": _s(bank.get("ifsc")),
                        "city": _s(bank.get("city")),
                    }
                    if isinstance(bank, dict) and bank
                    else None
                ),
            }
        )

    for row in dt_summary:
        positions.append(
            {
                "amc_name": _s(row.get("amcName")),
                "folio": _s(row.get("folio")),
                "scheme_name": _s(row.get("schemeName")),
                "isin": _s(row.get("isin")),
                "asset_type": _s(row.get("assetType")),
                "nav": _f(row.get("nav")) or None,
                "nav_date": _date(row.get("lastNavDate")),
                "units": _f(row.get("closingBalance")),
                "opening_units": _f(row.get("openingBal")) or None,
                "market_value": _f(row.get("marketValue")),
                "cost_value": _f(row.get("costValue")) or None,
                "is_demat": _s(row.get("isDemat")),
                "rta_name": _s(row.get("rtaCode")),
                "broker_code": _s(row.get("brokerCode")),
                "broker_name": _s(row.get("brokerName")),
                "kyc_status": _s(row.get("kycStatus")),
                "nominee_status": _s(row.get("nomineeStatus")),
                "tax_status": _s(row.get("taxStatus")),
                "last_txn_date": _date(row.get("lastTrxnDate")),
                "allows": None,
                "bank": None,
            }
        )

    transactions = []
    for row in dt_transactions:
        units = _f(row.get("trxnUnits"))
        transactions.append(
            {
                "date": _date(row.get("trxnDate")),
                "posted_date": _date(row.get("postedDate")),
                "amc_name": _s(row.get("amcName")),
                "folio": _s(row.get("folio")),
                "scheme_name": _s(row.get("schemeName")),
                "isin": _s(row.get("isin")),
                "description": _s(row.get("trxnDesc")),
                "amount": _f(row.get("trxnAmount")),
                "units": units,
                "nav": _f(row.get("purchasePrice")) or None,
                "stamp_duty": _f(row.get("stampDuty")) or None,
                "stt": _f(row.get("sttTax")) or None,
                "total_tax": _f(row.get("totalTax")) or None,
                "kind": classify_transaction(
                    _s(row.get("trxnDesc")), units, _s(row.get("trxnTypeFlag"))
                ),
            }
        )
    transactions.sort(key=lambda t: _sort_date(t.get("date")))

    amc_summary = [
        {
            "amc": _s(row.get("amc")),
            "amc_name": _s(row.get("amcName")),
            "market_value": _f(row.get("currentMktValue")),
            "cost_value": _f(row.get("costValue")),
            "gain_loss": _f(row.get("gainLoss")),
            "gain_loss_pct": _f(row.get("gainLossPercentage")),
            "is_demat": _s(row.get("isDemat")),
        }
        for row in _iter_blocks(payload, "summary")
    ]

    portfolio = [
        {
            "market_value": _f(row.get("currentMktValue")),
            "cost_value": _f(row.get("costValue")),
            "gain_loss": _f(row.get("gainLoss")),
            "gain_loss_pct": _f(row.get("gainLossPercentage")),
            "is_demat": _s(row.get("isDemat")),
        }
        for row in (payload.get("portfolio") or [])
        if isinstance(row, dict)
    ]

    investor = _investor_info(payload)
    investor["pan"] = _mask_pan(investor.get("pan"))

    return {
        "variant": "detailed" if dt_transactions else "summary",
        "investor": investor,
        "statement_from": _date(payload.get("fromDate")),
        "statement_to": _date(payload.get("toDate")),
        "amc_summary": amc_summary,
        "portfolio": portfolio,
        "positions": positions,
        "transactions": transactions,
        "counts": {
            "positions": len(positions),
            "transactions": len(transactions),
            "folios": len({(_s(p.get("folio")) or "") for p in positions}),
            "amcs": len({(_s(p.get("amc_name")) or "") for p in positions}),
        },
        "error_message": _s(payload.get("errorMessage")),
    }


def _mask_pan(pan: Optional[str]) -> Optional[str]:
    """``ABCDE1234F`` → ``ABCDE****F``. The import screen only needs to confirm
    which PAN the statement came back for, not to reprint it."""
    if not pan or len(pan) < 10:
        return pan
    return f"{pan[:5]}****{pan[9:]}"


def _mask_account(account: Optional[str]) -> Optional[str]:
    """Last four digits only. MFC returns the full registered bank account, and
    nothing downstream has a reason to see it — the mandate's identity is enough
    to tell two folios apart."""
    if not account:
        return None
    digits = account.strip()
    return f"****{digits[-4:]}" if len(digits) > 4 else "****"
