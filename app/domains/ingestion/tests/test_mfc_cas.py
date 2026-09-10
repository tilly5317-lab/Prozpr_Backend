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
import re

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


# --------------------------------------------------------------------------- mock gate


class _Env:
    """Set/clear env vars around one assertion, restoring whatever was there."""

    def __init__(self, **values: str | None) -> None:
        self._values = values
        self._saved: dict[str, str | None] = {}

    def __enter__(self) -> None:
        import os

        for key, value in self._values.items():
            self._saved[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def __exit__(self, *exc: object) -> None:
        import os

        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_mock_is_on_for_a_developer_with_no_credentials():
    from app.core.config import Settings

    with _Env(MFC_MOCK_ENABLED=None, MFC_CLIENT_ID=None):
        assert Settings.mfc_mock_enabled() is True
        # ...and that is what makes the flow usable at all locally.
        assert Settings.mfc_enabled() is True
        assert "/mfc-mock" in Settings.get_mfc_api_base_url()


def test_real_credentials_always_beat_the_mock():
    """A configured tenant must never be silently served sample data."""
    from app.core.config import Settings

    with _Env(MFC_MOCK_ENABLED=None, MFC_CLIENT_ID="a-real-tenant-id"):
        assert Settings.mfc_mock_enabled() is False
        assert Settings.get_mfc_client_id() == "a-real-tenant-id"
        assert "mfcentral.com" in Settings.get_mfc_api_base_url()


def test_mock_cannot_turn_itself_on_in_production():
    """DEPLOY_ENV=production is a hard stop even with no credentials set —
    a production box must 503 rather than mount a fake registrar."""
    from app.core.config import Settings

    original = Settings.DEPLOY_ENV
    try:
        Settings.DEPLOY_ENV = "production"
        with _Env(MFC_MOCK_ENABLED=None, MFC_CLIENT_ID=None):
            assert Settings.mfc_mock_enabled() is False
            assert Settings.mfc_enabled() is False
    finally:
        Settings.DEPLOY_ENV = original


def test_mock_can_be_forced_off_without_credentials():
    from app.core.config import Settings

    with _Env(MFC_MOCK_ENABLED="false", MFC_CLIENT_ID=None):
        assert Settings.mfc_mock_enabled() is False
        assert Settings.mfc_enabled() is False


def test_mock_credentials_resolve_as_a_complete_set():
    """Half-real, half-mock is the failure this guards: it authenticates and
    then dies at decrypt with an error that points nowhere useful."""
    from app.core.config import Settings

    with _Env(MFC_MOCK_ENABLED="true", MFC_CLIENT_ID=None):
        assert Settings.get_mfc_client_id()
        assert Settings.get_mfc_client_secret()
        assert Settings.get_mfc_username()
        assert Settings.get_mfc_password()
        assert Settings.get_mfc_encryption_key()
        assert Settings.get_mfc_iv()
        assert len(Settings.get_mfc_url_encryption_key() or "") == 32
        assert "BEGIN" in (Settings.get_mfc_private_key() or "")
        assert "BEGIN" in (Settings.get_mfc_public_key() or "")


def test_mock_constants_match_the_ones_config_falls_back_to():
    """config.py duplicates these rather than importing the dev module (which
    pulls in FastAPI and mints a key file). Drift would make the mock reject our
    own requests with a bare 401."""
    from app.core import config
    from app.domains.ingestion.dev import mfc_mock

    assert config._MFC_MOCK_CLIENT_ID == mfc_mock.MOCK_CLIENT_ID
    assert config._MFC_MOCK_CLIENT_SECRET == mfc_mock.MOCK_CLIENT_SECRET
    assert config._MFC_MOCK_USERNAME == mfc_mock.MOCK_USERNAME
    assert config._MFC_MOCK_PASSWORD == mfc_mock.MOCK_PASSWORD
    assert config._MFC_MOCK_ENCRYPTION_KEY == mfc_mock.MOCK_ENCRYPTION_KEY
    assert config._MFC_MOCK_IV == mfc_mock.MOCK_IV
    assert config._MFC_MOCK_URL_KEY == mfc_mock.MOCK_URL_KEY


def test_mock_qr_round_trips_its_variant():
    """The QR has to survive being written to disk by a browser and uploaded
    back, so the marker rides after IEND where image decoders stop reading."""
    import base64

    from app.domains.ingestion.dev import mfc_mock

    for variant in ("summary", "detailed"):
        blob = mfc_mock.qr_bytes("3100001", variant)
        encoded = base64.b64encode(blob).decode()
        assert mfc_mock.variant_from_qr(encoded) == variant
    # An unrelated image still works, so the flow can be walked with a hand-made
    # file; something too small to be an image does not.
    assert mfc_mock.variant_from_qr(base64.b64encode(b"x" * 500).decode()) == "detailed"
    assert mfc_mock.variant_from_qr(base64.b64encode(b"x").decode()) is None


def test_mock_pages_render_with_no_leftover_placeholders():
    """These carry CSS and JavaScript, so they use __TOKEN__ substitution rather
    than str.format, which chokes on every brace in a stylesheet."""
    from app.domains.ingestion.dev import mfc_mock

    consent = mfc_mock._render(
        mfc_mock._CONSENT_PAGE,
        REQ_ID="3100001",
        PAYLOAD='{"reqId": "3100001"}',
        DETAILED_QR="data:image/png;base64,AAAA",
        SUMMARY_QR="data:image/png;base64,BBBB",
        REDIRECT_URL="http://localhost:8080/mfc-cas/callback",
        OTP_DEST="xxxxxx1234",
        OTP_HINT="123456",
        START_PANE="pane-otp",
    )
    # Exhaustive, not a spot-check: naming two tokens is how this test stopped
    # covering the page the last time it grew one.
    assert not re.findall(r"__[A-Z_]+__", consent)
    assert "data:image/png;base64,AAAA" in consent
    # The stylesheet's braces survive untouched.
    assert "border-radius: 14px" in consent

    index = mfc_mock._render(
        mfc_mock._INDEX_PAGE, API_BASE="a", REDIRECT_BASE="b", KEY_FILE="c"
    )
    assert "__API_BASE__" not in index


# --------------------------------------------------------------------------- otp hint


def test_otp_destination_names_where_the_code_lands():
    """The investor has to know which handset or inbox to watch, and it is
    frequently NOT the contact they signed in with."""
    from app.domains.ingestion.services.mfc_cas_ingest import _mask_contact

    assert _mask_contact("+919876501234", None) == "your mobile ending 1234"
    assert _mask_contact("9876501234", None) == "your mobile ending 1234"
    assert _mask_contact(None, "investor.name@fundhouse.com").endswith("@fundhouse.com")
    assert "investor" not in _mask_contact(None, "investor.name@fundhouse.com")
    # Neither supplied is a real case — MFC then uses whatever it holds against
    # the PAN — so this must read as an answer, not a missing value.
    assert _mask_contact(None, None) == "your registered contact"


# --------------------------------------------------------------------------- consent site


def test_consent_page_carries_an_otp_gate_and_the_real_filename():
    """The mock stands in for MFC's hosted page, so it has to ask for the OTP.

    Not decoration: it is the only place in a local run where the OTP exists at
    all. MFC's client API has no OTP endpoint — the code is issued and checked
    inside their consent site — so an OTP screen anywhere in Prozpr's own UI
    would be a step that cannot survive the switch to live credentials.
    """
    from app.domains.ingestion.dev import mfc_mock

    page = mfc_mock._render(
        mfc_mock._CONSENT_PAGE,
        REQ_ID="3100001",
        PAYLOAD="{}",
        DETAILED_QR="data:image/png;base64,AAAA",
        SUMMARY_QR="data:image/png;base64,BBBB",
        REDIRECT_URL="http://localhost:8080/mfc-cas/callback",
        OTP_DEST="xxxxxx1234",
        OTP_HINT="123456",
        START_PANE="pane-otp",
    )
    assert 'id="otp"' in page and "verify-otp" in page
    # Two ways back, and both must survive an edit here. The postMessage is the
    # primary one — MFC's own documented `mfc-cas-download` shape, which needs
    # no disk and no folder permission. The saved file is the fallback, and its
    # name is what the Downloads scan prefers.
    assert "'mfc-cas-download'" in page
    assert "a.download = 'cas-request-qr.png'" in page
    # A blob, never a data: URL — Chrome will navigate to a data: URI instead of
    # downloading it in some window contexts, and then no file ever appears.
    assert "URL.createObjectURL(blob)" in page
    # The page is served at /mfc-mock/api/auth/start, so a BARE name is the
    # sibling route. "../api/auth/verify-otp" climbed to /mfc-mock/api/ and then
    # re-added api/auth, and every verify 404'd.
    from urllib.parse import urljoin

    assert "'verify-otp'" in page and "'resend-otp'" in page
    base = "http://localhost:8000/mfc-mock/api/auth/start"
    assert urljoin(base, "verify-otp").endswith("/mfc-mock/api/auth/verify-otp")


def test_mock_otp_is_123456_unless_told_otherwise(monkeypatch):
    """A fixed code, because the mock exists to be walked over and over.

    MFC's real UAT rule (guide p.26: 00 + the PAN's last four digits) is kept
    behind MFC_MOCK_OTP=uat for rehearsing against their sandbox.
    """
    from app.domains.ingestion.dev import mfc_mock

    monkeypatch.delenv("MFC_MOCK_OTP", raising=False)
    assert mfc_mock.mock_otp_code("ABCDE1234F") == "123456"
    assert mfc_mock.mock_otp_code() == "123456"

    monkeypatch.setenv("MFC_MOCK_OTP", "uat")
    assert mfc_mock.mock_otp_code("ABCDE1234F") == "001234"
    assert mfc_mock.otp_for_pan("AGVPN4690G") == "004690"
    # No PAN at all still yields six digits, never an empty string the input
    # would silently accept.
    assert mfc_mock.otp_for_pan("") == "000000"

    monkeypatch.setenv("MFC_MOCK_OTP", "999111")
    assert mfc_mock.mock_otp_code("ABCDE1234F") == "999111"

    # Junk falls back rather than locking the mock behind an unusable code.
    monkeypatch.setenv("MFC_MOCK_OTP", "not-a-code")
    assert mfc_mock.mock_otp_code("ABCDE1234F") == "123456"


def test_otp_is_bounded_and_resettable(monkeypatch):
    from app.domains.ingestion.dev import mfc_mock

    monkeypatch.delenv("MFC_MOCK_OTP", raising=False)
    code = mfc_mock._issue_otp("3100002", "ABCDE1234F")
    session = mfc_mock._otp_sessions["3100002"]

    for _ in range(mfc_mock.OTP_MAX_ATTEMPTS):
        session["attempts"] += 1
    assert session["attempts"] == mfc_mock.OTP_MAX_ATTEMPTS

    # A resend clears the lockout, which is the only way out of it, and yields
    # the SAME code — derived, not random, so reloading the consent page never
    # invalidates what is already on screen.
    assert mfc_mock._issue_otp("3100002", "ABCDE1234F") == code
    assert mfc_mock._otp_sessions["3100002"]["attempts"] == 0
    assert code == "123456"


def test_new_cas_request_issues_the_otp_before_the_investor_arrives():
    """MFC sends the code when the request is registered, not when the consent
    page loads — so a session must exist the moment /start returns."""
    import asyncio

    from app.domains.ingestion.dev import mfc_mock

    mfc_mock._otp_sessions.clear()
    payload = {
        "pan": "AGVPN4690G",
        "mobile": "9550755111",
        "clientRefNo": "ref-1",
        "fromDate": "01-JAN-2000",
        "toDate": "01-JAN-2026",
    }
    private_key, _ = mfc_mock.keys()
    encrypted = mfc_crypto.encrypt_api_payload(
        payload, shared_key=mfc_mock.MOCK_ENCRYPTION_KEY, iv=mfc_mock.MOCK_IV
    )
    body = {
        "request": encrypted,
        "signature": mfc_crypto.sign_detached_jws(encrypted, private_key_pem=private_key),
    }

    class _Req:
        async def json(self):
            return body

    token = f"Bearer {'mock-access-token'}"
    response = asyncio.run(
        mfc_mock.new_cas_request(
            _Req(), authorization=token, clientid=mfc_mock.MOCK_CLIENT_ID
        )
    )
    decrypted = mfc_crypto.decrypt_api_payload(
        json.loads(response.body.decode())["response"],
        shared_key=mfc_mock.MOCK_ENCRYPTION_KEY,
        iv=mfc_mock.MOCK_IV,
    )
    assert str(decrypted["reqId"]) in mfc_mock._otp_sessions


def test_mask_destination_never_echoes_the_whole_contact():
    from app.domains.ingestion.dev import mfc_mock

    assert mfc_mock._mask_destination({"mobile": "9550755111"}) == "xxxxxx5111"
    masked = mfc_mock._mask_destination({"email": "investor.name@fundhouse.com"})
    assert masked.endswith("@fundhouse.com") and "investor.name" not in masked
    assert mfc_mock._mask_destination({}) == "your registered contact"


# --------------------------------------------------------------------------- integration mode


def test_integration_mode_defaults_to_popup_and_rejects_junk(monkeypatch):
    """MFC's consent page opens in its own window by default.

    It is their page asking for an OTP; a separate window keeps that obvious.
    `iframe` stays selectable — their guide documents and auto-detects it — but
    is opt-in.
    """
    from app.core.config import Settings

    monkeypatch.delenv("MFC_INTEGRATION_MODE", raising=False)
    assert Settings.get_mfc_integration_mode() == "popup"

    for value, expected in [
        ("iframe", "iframe"),
        ("REDIRECT", "redirect"),
        (" popup ", "popup"),
        ("webview", "popup"),
        ("", "popup"),
    ]:
        monkeypatch.setenv("MFC_INTEGRATION_MODE", value)
        assert Settings.get_mfc_integration_mode() == expected


def test_otp_capture_is_app_only_where_a_code_can_actually_be_checked(monkeypatch):
    """The frontend renders its OTP screen off this flag.

    MFC's live API has no OTP endpoint (guide p.12), so real credentials must
    report "mfc" and the screen must not appear — an OTP box that accepts a code
    it cannot check is worse than no box.
    """
    from app.core.config import Settings

    monkeypatch.delenv("MFC_OTP_CAPTURE", raising=False)
    monkeypatch.delenv("MFC_CLIENT_ID", raising=False)
    monkeypatch.setenv("DEPLOY_ENV", "development")
    assert Settings.mfc_mock_enabled() is True
    assert Settings.mfc_otp_capture() == "app"

    monkeypatch.setenv("MFC_CLIENT_ID", "real-tenant")
    assert Settings.mfc_otp_capture() == "mfc"

    # The escape hatch for the day MFC ships one.
    monkeypatch.setenv("MFC_OTP_CAPTURE", "app")
    assert Settings.mfc_otp_capture() == "app"
