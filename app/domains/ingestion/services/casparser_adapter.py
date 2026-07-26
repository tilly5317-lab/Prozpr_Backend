"""Adapter — CAS Parser API (casparser.in v4) unified JSON → legacy parsed dict.

The whole CAMS ingest pipeline (`cams_cas_ingest`) consumes the dict shape the
old in-process ``casparser`` library produced::

    {
      "cas_type": "DETAILED"|"SUMMARY", "file_type": "...",
      "statement_period": {"from", "to"},
      "investor_info": {"name", "email", "mobile", "address"},
      "folios": [{"folio", "amc", "PAN",
                  "schemes": [{"scheme", "amfi", "isin", "type", "rta_code",
                               "open", "close",
                               "valuation": {"value","cost","nav","date"},
                               "transactions": [{"date","amount","units","nav",
                                                 "description","type",
                                                 "stamp_duty","stt"}]}]}]
    }

The v4 API instead returns ``meta / investor / summary / mutual_funds`` (see
https://casparser.in/docs/api-reference/cas-parser/parse-camskfintech-cas-pdf).
This module maps the new shape onto the old one so every downstream invariant —
transaction-derived holdings, statement-balance guards, the SUMMARY/zero-value
422 rejections, audit rows — keeps working unchanged. Pure transform: no I/O.

Transaction ``type`` values are the same enum family both before and after
(PURCHASE, PURCHASE_SIP, REDEMPTION, SWITCH_IN/OUT, DIVIDEND_REINVEST, ...),
so the ``_TXN_TYPE_FLAG`` allow-list keeps matching.
"""

from __future__ import annotations

from typing import Any, Optional


class CasResponseShapeError(Exception):
    """The parse response is valid JSON but not a mutual-fund CAS we can ingest."""

    def __init__(self, message: str, *, demat_statement: bool = False) -> None:
        super().__init__(message)
        # True when the PDF parsed fine but is a CDSL/NSDL demat e-CAS with no
        # MF folio section — the caller words the user-facing error differently.
        self.demat_statement = demat_statement


def _s(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# v4 tags schemes "Equity" / "Debt" / "Hybrid" / "Other"; the legacy pipeline
# feeds this into `resolve_asset_bucket`, which matches on uppercase tokens and
# treats N/A-ish values as "classify from the name instead".
_SCHEME_TYPE_MAP = {
    "EQUITY": "EQUITY",
    "DEBT": "DEBT",
    "HYBRID": "HYBRID",
}


def _map_scheme_type(value: object) -> str:
    return _SCHEME_TYPE_MAP.get((_s(value) or "").upper(), "N/A")


def _map_transaction(txn: dict[str, Any]) -> dict[str, Any]:
    extra = txn.get("additional_info") or {}
    return {
        "date": txn.get("date"),
        "description": txn.get("description"),
        "amount": txn.get("amount"),
        "units": txn.get("units"),
        "nav": txn.get("nav"),
        "balance": txn.get("balance"),
        "type": txn.get("type"),
        "dividend_rate": txn.get("dividend_rate"),
        # Not first-class fields in the v4 response; present for some sources
        # under additional_info. None is fine — the audit row stores NULL.
        "stamp_duty": extra.get("stamp_duty"),
        "stt": extra.get("stt"),
    }


def _map_scheme(scheme: dict[str, Any], statement_to: Optional[str]) -> dict[str, Any]:
    extra = scheme.get("additional_info") or {}
    transactions = [
        _map_transaction(t) for t in (scheme.get("transactions") or []) if t
    ]

    close_units = extra.get("close_units")
    if close_units is None:
        close_units = scheme.get("units")
    if close_units is None and transactions:
        # Last resort: the ledger's own running balance.
        close_units = transactions[-1].get("balance")

    gain = scheme.get("gain") or {}
    cost = scheme.get("cost")
    if cost is None and scheme.get("value") is not None:
        absolute = gain.get("absolute")
        if isinstance(absolute, (int, float)):
            cost = float(scheme["value"]) - float(absolute)

    return {
        "scheme": scheme.get("name"),
        "isin": scheme.get("isin"),
        "amfi": extra.get("amfi"),
        "type": _map_scheme_type(scheme.get("type")),
        "rta_code": extra.get("rta_code"),
        "advisor": extra.get("advisor"),
        "open": extra.get("open_units"),
        "close": close_units,
        "valuation": {
            "value": scheme.get("value"),
            "cost": cost,
            "nav": scheme.get("nav"),
            "date": statement_to,
        },
        "transactions": transactions,
    }


def to_legacy_parsed(payload: dict[str, Any]) -> dict[str, Any]:
    """Map a `/v4/smart/parse` (or `/v4/cams_kfintech/parse`) response onto the
    legacy parsed-CAS dict. Raises :class:`CasResponseShapeError` when the
    statement carries no mutual-fund folio section (e.g. a pure demat e-CAS)."""
    meta = payload.get("meta") or {}
    period = meta.get("statement_period") or {}
    investor = payload.get("investor") or {}
    mutual_funds = payload.get("mutual_funds") or []

    # A CDSL/NSDL demat e-CAS may list MF folio holdings, but never carries the
    # full transaction ledger the pipeline is built on — same rejection the old
    # in-process parser applied.
    source = (_s(meta.get("cas_type")) or "").upper()
    if source in ("CDSL", "NSDL"):
        raise CasResponseShapeError(
            f"This is a {source} demat e-CAS, not a CAMS/KFintech mutual-fund CAS.",
            demat_statement=True,
        )

    if not mutual_funds:
        if payload.get("demat_accounts"):
            raise CasResponseShapeError(
                "The statement contains only demat holdings — no mutual-fund folios.",
                demat_statement=True,
            )
        raise CasResponseShapeError(
            "No mutual-fund folios were found in this statement."
        )

    statement_to = _s(period.get("to"))
    investor_pan = _s(investor.get("pan"))

    folios: list[dict[str, Any]] = []
    has_transactions = False
    for folio in mutual_funds:
        if not isinstance(folio, dict):
            continue
        folio_extra = folio.get("additional_info") or {}
        schemes = [
            _map_scheme(s, statement_to)
            for s in (folio.get("schemes") or [])
            if isinstance(s, dict)
        ]
        if any(s["transactions"] for s in schemes):
            has_transactions = True
        folios.append(
            {
                "folio": folio.get("folio_number"),
                "amc": folio.get("amc"),
                "PAN": folio_extra.get("pan") or investor_pan,
                "registrar": folio.get("registrar"),
                "schemes": schemes,
            }
        )

    return {
        # A CAS without a single transaction anywhere is a holdings-only
        # Summary statement — the ingest pipeline 422s it with instructions to
        # request the Detailed variant.
        "cas_type": "DETAILED" if has_transactions else "SUMMARY",
        "file_type": _s(meta.get("cas_type")) or "CAMS_KFINTECH",
        "statement_period": {"from": _s(period.get("from")), "to": statement_to},
        "investor_info": {
            "name": investor.get("name"),
            "email": investor.get("email"),
            "mobile": investor.get("mobile"),
            "address": investor.get("address"),
        },
        "folios": folios,
    }
