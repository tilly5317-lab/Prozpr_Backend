"""MF Central integration — the crypto envelope and the payload adapter.

Both halves are things we cannot test against MFC without live credentials, and
both fail in ways that look like someone else's bug:

* a key-derivation slip produces valid base64 that MFC rejects with an opaque
  422, so the round-trips here are the only local proof the envelope is right;
* the adapter turns MFC's flat, enum-less JSON into the shape the whole ingest
  pipeline assumes, and a mis-mapped unit sign or a dropped transaction type
  corrupts a portfolio quietly rather than raising.

The payloads below are MFC's own samples, copied from their integration guide
(pages 15-23), trimmed but not reshaped.
"""

from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.domains.ingestion.services import mfc_crypto
from app.domains.ingestion.services.casparser_adapter import CasResponseShapeError
from app.domains.ingestion.services.mfc_cas_adapter import (
    classify_transaction,
    summarize_for_display,
    to_legacy_parsed,
)


# --------------------------------------------------------------------------- fixtures

SHARED_KEY = "a-shared-encryption-key-from-mfc"
IV = "1234567890123456"
# Exactly 32 characters — the raw URL key, MFC's published UAT value.
URL_KEY = "odLZvcdVAhq+Re8UstN8u928xRQTE6ym"


@pytest.fixture(scope="module")
def rsa_keys() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_pem, public_pem


SUMMARY_PAYLOAD = {
    "reqId": "3122359",
    "pan": "AGVPN4690G",
    "mobile": "9550755111",
    "email": "",
    "pekrn": "",
    "data": [
        {
            "summary": [
                {
                    "amc": "108",
                    "amcName": "UTI MUTUAL FUND",
                    "currentMktValue": 16942.16,
                    "costValue": 14999.25,
                    "gainLoss": 1942.91,
                    "gainLossPercentage": 13,
                    "isDemat": "N",
                }
            ],
            "schemes": [
                {
                    "amc": "108",
                    "amcName": "UTI MUTUAL FUND",
                    "folio": "599366447236",
                    "investorName": "John Doe",
                    "mobile": "9550755111",
                    "email": "john@example.com",
                    "taxStatus": "01",
                    "modeOfHolding": "SINGLE",
                    "schemeCode": "EQGP",
                    "schemeName": "UTI Flexi Cap Fund - Regular Plan",
                    "schemeOption": "GROWTH",
                    "assetType": "EQUITY",
                    "schemeType": "EQUITY FUND",
                    "nav": 321.7826,
                    "navDate": "01-Jan-2026",
                    "closingBalance": 16.521,
                    "availableUnits": 16.521,
                    "currentMktValue": 5316.17,
                    "costValue": 4999.75,
                    "gainLoss": 316.42,
                    "gainLossPercentage": 6.3,
                    "isin": "INF789F01513",
                    "brokerCode": "ARN-154065",
                    "brokerName": "A Distributor",
                    "isDemat": "N",
                    "purAllow": "Y",
                    "redAllow": "Y",
                    "swtAllow": "Y",
                    "sipAllow": "Y",
                    "stpAllow": "N",
                    "swpAllow": "N",
                    "planMode": "REGULAR",
                    "rtaName": "KFIN",
                    "nomineeStatus": "Y",
                    "kycStatus": "02",
                    "bank": {
                        "accountNo": "068001510540",
                        "accountType": "SAVING",
                        "name": "ICICI BANK LIMITED",
                        "city": "HYDERABAD",
                        "ifsc": "ICIC0000680",
                        "neftIfsc": "ICIC0000680",
                    },
                }
            ],
        }
    ],
    "errorMessage": "",
    "portfolio": [
        {
            "currentMktValue": "16942.16",
            "costValue": "14999.25",
            "gainLoss": "1942.91",
            "gainLossPercentage": "12.95",
            "isDemat": "N",
        },
        {
            "currentMktValue": "0.00",
            "costValue": "0.00",
            "gainLoss": "0.00",
            "gainLossPercentage": "0.00",
            "isDemat": "Y",
        },
    ],
    "investorDetails": {},
    "statementHoldingFilter": None,
}


DETAILED_PAYLOAD = {
    "reqId": "2638435-590807058",
    "pan": "CFRPP0494R",
    "pekrn": "",
    "email": "",
    "fromDate": "01-Apr-1980",
    "toDate": "29-Aug-2024",
    "data": [
        {
            "dtTransaction": [
                {
                    "amc": "H",
                    "amcName": "HDFC Mutual Fund",
                    "folio": "17064219",
                    "trxnDate": "24-SEP-2020",
                    "postedDate": "24-SEP-2020",
                    "scheme": "02",
                    "schemeName": "HDFC Flexi Cap Fund - Regular Plan - Growth",
                    "trxnDesc": "Address Updated from KRA Data",
                    "trxnAmount": "0",
                    "trxnUnits": "0",
                    "purchasePrice": "0",
                    "sttTax": "0",
                    "stampDuty": "0",
                    "trxnTypeFlag": "",
                    "isin": "INF179K01608",
                },
                {
                    "amc": "H",
                    "amcName": "HDFC Mutual Fund",
                    "folio": "17064219",
                    "trxnDate": "05-MAY-2022",
                    "postedDate": "05-MAY-2022",
                    "scheme": "02",
                    "schemeName": "HDFC Flexi Cap Fund - Regular Plan - Growth",
                    "trxnDesc": "Systematic Investment Purchase",
                    "trxnAmount": "38000",
                    "trxnUnits": "43.614",
                    "purchasePrice": "871.28",
                    "sttTax": "0",
                    "stampDuty": "1.9",
                    "trxnTypeFlag": "",
                    "isin": "INF179K01608",
                },
                {
                    "amc": "H",
                    "amcName": "HDFC Mutual Fund",
                    "folio": "17064219",
                    "trxnDate": "04-FEB-2021",
                    "postedDate": "04-FEB-2021",
                    "scheme": "54",
                    "schemeName": "HDFC Low Duration Fund - Regular Plan - Growth",
                    "trxnDesc": "Purchase",
                    "trxnAmount": "60000",
                    "trxnUnits": "1339.240",
                    "purchasePrice": "44.80",
                    "sttTax": "0",
                    "stampDuty": "3",
                    "trxnTypeFlag": "",
                    "isin": "INF179K01442",
                },
            ],
            "dtSummary": [
                {
                    "amc": "H",
                    "amcName": "HDFC Mutual Fund",
                    "folio": "17064219",
                    "scheme": "02",
                    "schemeName": "HDFC Flexi Cap Fund - Regular Plan - Growth",
                    "kycStatus": "1",
                    "brokerCode": "ARN-48944",
                    "brokerName": "A Distributor",
                    "rtaCode": "CAMS",
                    "lastTrxnDate": "05-MAY-2022",
                    "openingBal": "0.000",
                    "marketValue": "6542.10",
                    "nav": "150.000",
                    "closingBalance": "43.614",
                    "lastNavDate": "11-JUL-2024",
                    "isDemat": "N",
                    "assetType": "EQUITY",
                    "isin": "INF179K01608",
                    "nomineeStatus": "N",
                    "taxStatus": "01",
                    "costValue": "38000",
                },
                {
                    "amc": "H",
                    "amcName": "HDFC Mutual Fund",
                    "folio": "17064219",
                    "scheme": "54",
                    "schemeName": "HDFC Low Duration Fund - Regular Plan - Growth",
                    "kycStatus": "1",
                    "brokerCode": "ARN-48944",
                    "brokerName": "A Distributor",
                    "rtaCode": "CAMS",
                    "lastTrxnDate": "04-FEB-2021",
                    "openingBal": "0.000",
                    "marketValue": "133924.00",
                    "nav": "100.0000",
                    "closingBalance": "1339.240",
                    "lastNavDate": "12-AUG-2024",
                    "isDemat": "N",
                    "assetType": "DEBT",
                    "isin": "INF179K01442",
                    "nomineeStatus": "N",
                    "taxStatus": "01",
                    "costValue": "60000",
                },
            ],
        }
    ],
    "investorDetails": {
        "address": {
            "address1": "12 Some Street",
            "address2": "Near Somewhere",
            "address3": "",
            "city": "HOWRAH",
            "state": "West Bengal",
            "pincode": "711204",
            "country": "India",
        },
        "mobile": "+917874806606",
        "investorFirstName": "JQLVAAIJNJSI",
        "investorMiddleName": "",
        "investorLastName": "",
        "email": "investor@example.com",
    },
}


# --------------------------------------------------------------------------- crypto


def test_api_envelope_round_trips():
    payload = {"clientRefNo": "prozpr123", "pan": "ABCDE1234F", "reqId": ""}
    blob = mfc_crypto.encrypt_api_payload(payload, shared_key=SHARED_KEY, iv=IV)
    assert mfc_crypto.decrypt_api_payload(blob, shared_key=SHARED_KEY, iv=IV) == payload


def test_api_envelope_uses_the_hashed_key_not_the_raw_one():
    """The API key is SHA-256(shared)[:32]; the URL key is raw.

    Swapping them is the integration's classic failure: both produce valid
    base64 and MFC answers with an opaque 422, so this pins the derivation.
    """
    blob = mfc_crypto.encrypt_api_payload({"a": 1}, shared_key=SHARED_KEY, iv=IV)
    raw = base64.b64decode(blob)
    expected = mfc_crypto._aes_cbc_encrypt(  # noqa: SLF001 — asserting the derivation
        mfc_crypto.derive_api_key(SHARED_KEY),
        mfc_crypto.resolve_iv(IV),
        json.dumps({"a": 1}, separators=(",", ":")).encode(),
    )
    assert raw == expected


def test_decrypt_accepts_url_safe_base64():
    """MFC's own /api/test/* helpers return URL-safe, unpadded base64 while the
    client APIs return standard base64. Both have to decode."""
    blob = mfc_crypto.encrypt_api_payload({"x": "y"}, shared_key=SHARED_KEY, iv=IV)
    urlsafe = blob.replace("+", "-").replace("/", "_").rstrip("=")
    assert mfc_crypto.decrypt_api_payload(urlsafe, shared_key=SHARED_KEY, iv=IV) == {
        "x": "y"
    }


def test_iv_accepts_all_three_documented_forms():
    raw = mfc_crypto.resolve_iv(IV)
    assert raw == mfc_crypto.resolve_iv(raw.hex())
    assert raw == mfc_crypto.resolve_iv(base64.b64encode(raw).decode())


def test_url_envelope_round_trips_and_uses_a_fresh_iv():
    payload = {"reqId": "3102549", "otpRef": "abc", "pan": "ABCDE1234F"}
    first = mfc_crypto.encrypt_redirect_payload(payload, url_key=URL_KEY)
    second = mfc_crypto.encrypt_redirect_payload(payload, url_key=URL_KEY)

    assert first != second, "the redirect IV must be random per request"
    assert first.split(":")[0] != second.split(":")[0]
    assert mfc_crypto.decrypt_redirect_payload(first, url_key=URL_KEY) == payload


def test_url_key_must_be_exactly_32_characters():
    with pytest.raises(mfc_crypto.MfcCryptoError):
        mfc_crypto.encrypt_redirect_payload({"a": 1}, url_key="too-short")


def test_detached_jws_verifies_over_the_verbatim_ciphertext(rsa_keys):
    private_pem, public_pem = rsa_keys
    encrypted = mfc_crypto.encrypt_api_payload({"k": "v"}, shared_key=SHARED_KEY, iv=IV)

    signature = mfc_crypto.sign_detached_jws(encrypted, private_key_pem=private_pem)
    header, payload_segment, _ = signature.split(".")

    assert payload_segment == "", "RFC 7797 leaves the payload segment empty"
    assert json.loads(base64.urlsafe_b64decode(header + "=="))["b64"] is False
    assert mfc_crypto.verify_signature(signature, encrypted, public_key_pem=public_pem)
    # A signature is only good for the exact body it covers.
    assert not mfc_crypto.verify_signature(
        signature, encrypted + "x", public_key_pem=public_pem
    )


def test_compact_jwt_mode_also_verifies(rsa_keys):
    private_pem, public_pem = rsa_keys
    encrypted = "some-encrypted-blob"
    signature = mfc_crypto.sign_compact_jwt(encrypted, private_key_pem=private_pem)
    assert mfc_crypto.verify_signature(signature, encrypted, public_key_pem=public_pem)


def test_private_key_survives_escaped_newlines(rsa_keys):
    """The usual .env casualty: a PEM pasted as one line with literal \\n."""
    private_pem, public_pem = rsa_keys
    mangled = private_pem.replace("\n", "\\n")
    signature = mfc_crypto.sign_detached_jws("abc", private_key_pem=mangled)
    assert mfc_crypto.verify_signature(signature, "abc", public_key_pem=public_pem)


# --------------------------------------------------------------------------- adapter


def test_detailed_payload_becomes_an_ingestible_cas():
    parsed = to_legacy_parsed(DETAILED_PAYLOAD)

    assert parsed["cas_type"] == "DETAILED"
    assert parsed["file_type"] == "MFCENTRAL"
    assert parsed["statement_period"] == {"from": "01-Apr-1980", "to": "29-Aug-2024"}

    # One folio, because MFC repeats the folio on every row and the adapter
    # regroups rather than trusting the nesting.
    assert len(parsed["folios"]) == 1
    folio = parsed["folios"][0]
    assert folio["folio"] == "17064219"
    assert folio["PAN"] == "CFRPP0494R"
    assert len(folio["schemes"]) == 2

    equity = next(s for s in folio["schemes"] if s["isin"] == "INF179K01608")
    assert equity["type"] == "EQUITY"
    assert equity["close"] == pytest.approx(43.614)
    assert equity["valuation"]["value"] == pytest.approx(6542.10)
    assert equity["valuation"]["cost"] == pytest.approx(38000)

    # The address-update row moves no units and must not become a transaction.
    kinds = [t["type"] for t in equity["transactions"]]
    assert "UNKNOWN" in kinds and "PURCHASE_SIP" in kinds

    debt = next(s for s in folio["schemes"] if s["isin"] == "INF179K01442")
    assert debt["type"] == "DEBT"
    assert debt["transactions"][0]["type"] == "PURCHASE"
    assert debt["transactions"][0]["stamp_duty"] == pytest.approx(3.0)


def test_transactions_are_sorted_oldest_first():
    """`_populate_children` reads the LAST transaction as the scheme's most
    recent, and the snapshot walk assumes chronological order — MFC's ledger
    arrives in neither."""
    parsed = to_legacy_parsed(DETAILED_PAYLOAD)
    folio = parsed["folios"][0]
    equity = next(s for s in folio["schemes"] if s["isin"] == "INF179K01608")
    dates = [t["date"] for t in equity["transactions"]]
    assert dates == ["24-Sep-2020", "05-May-2022"]


def test_summary_payload_is_tagged_summary_so_the_pipeline_rejects_it():
    parsed = to_legacy_parsed(SUMMARY_PAYLOAD)

    assert parsed["cas_type"] == "SUMMARY"
    scheme = parsed["folios"][0]["schemes"][0]
    assert scheme["transactions"] == []
    assert scheme["isin"] == "INF789F01513"
    assert scheme["valuation"]["value"] == pytest.approx(5316.17)


def test_empty_payload_raises_a_shape_error():
    with pytest.raises(CasResponseShapeError):
        to_legacy_parsed({"reqId": "1", "pan": "ABCDE1234F", "data": []})


def test_error_message_is_surfaced_when_there_are_no_folios():
    with pytest.raises(CasResponseShapeError, match="No folios found for this PAN"):
        to_legacy_parsed(
            {"reqId": "1", "data": [], "errorMessage": "No folios found for this PAN"}
        )


@pytest.mark.parametrize(
    ("description", "units", "expected"),
    [
        ("Systematic Investment Purchase", 10.0, "PURCHASE_SIP"),
        ("SIP Instalment", 10.0, "PURCHASE_SIP"),
        ("Purchase", 10.0, "PURCHASE"),
        ("Redemption", 10.0, "REDEMPTION"),
        ("Switch Out - HDFC Top 100", -5.0, "SWITCH_OUT"),
        ("Switch In - HDFC Flexi Cap", 5.0, "SWITCH_IN"),
        ("IDCW Reinvestment", 1.2, "DIVIDEND_REINVEST"),
        ("IDCW Payout", 0.0, "DIVIDEND_PAYOUT"),
        ("Address Updated from KRA Data", 0.0, "UNKNOWN"),
        ("Nominee Registered", 0.0, "UNKNOWN"),
        ("*** Stamp Duty ***", 0.0, "STAMP_DUTY_TAX"),
    ],
)
def test_transaction_classification(description, units, expected):
    assert classify_transaction(description, units) == expected


def test_redemption_wins_over_a_positive_unit_count():
    """Some AMCs report redemption units unsigned. The description outranks the
    sign, or a sell is ingested as a buy and the position doubles."""
    assert classify_transaction("Redemption of units", 12.5) == "REDEMPTION"


def test_rta_supplied_flag_outranks_the_description():
    assert classify_transaction("Some Ambiguous Text", 0.0, "SO") == "SWITCH_OUT"


def test_units_sign_is_the_last_resort():
    assert classify_transaction("Unrecognised wording", 5.0) == "PURCHASE"
    assert classify_transaction("Unrecognised wording", -5.0) == "REDEMPTION"
    assert classify_transaction("Unrecognised wording", 0.0) == "UNKNOWN"


# --------------------------------------------------------------------------- display


def test_display_payload_carries_what_the_pipeline_throws_away():
    display = summarize_for_display(SUMMARY_PAYLOAD)

    assert display["variant"] == "summary"
    position = display["positions"][0]
    # The transactability flags exist nowhere in our schema, and they are the
    # reason to prefer MFC — a plan that ignores them proposes rejected trades.
    assert position["allows"] == {
        "purchase": True,
        "redeem": True,
        "switch": True,
        "sip": True,
        "stp": False,
        "swp": False,
    }
    assert position["kyc_status"] == "02"
    assert position["nominee_status"] == "Y"
    assert display["amc_summary"][0]["amc_name"] == "UTI MUTUAL FUND"
    assert len(display["portfolio"]) == 2  # demat + non-demat split


def test_display_masks_pan_and_bank_account():
    display = summarize_for_display(SUMMARY_PAYLOAD)
    assert display["investor"]["pan"] == "AGVPN****G"
    assert display["positions"][0]["bank"]["account_no"] == "****0540"
    assert display["positions"][0]["bank"]["ifsc"] == "ICIC0000680"


def test_display_of_a_detailed_payload_lists_every_ledger_row():
    display = summarize_for_display(DETAILED_PAYLOAD)

    assert display["variant"] == "detailed"
    # All three, including the non-unit-moving one: the display is the raw
    # statement, not the ingested subset.
    assert display["counts"]["transactions"] == 3
    assert display["counts"]["positions"] == 2
    assert display["investor"]["name"] == "JQLVAAIJNJSI"
    assert display["investor"]["mobile"] == "+917874806606"
    assert "HOWRAH" in (display["investor"]["address"] or "")
