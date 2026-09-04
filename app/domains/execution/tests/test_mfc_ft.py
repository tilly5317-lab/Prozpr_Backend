"""MF Central FT — code masters, payload shape, and the guards before submission.

These cover the parts that fail expensively and silently:

* **AMC resolution.** Our holdings carry SCHEME names; MFC wants a fund-house
  code. A wrong answer here submits an order against a different fund house,
  which is not a near miss.
* **Payload shape.** MFC validates strings, an ``otpSentTo``/``otpMobile``
  agreement, and per-family field sets, and rejects each with a 400 that names
  no field. Building the payload wrong is the single likeliest way this
  integration breaks in UAT.
* **Pre-submission guards.** MFC's rejections arrive AFTER the order exists on
  their side, so anything we can refuse locally we should.

Nothing here talks to MFC. The live chain is walked against the mounted mock.
"""

from __future__ import annotations

import pytest

from app.domains.execution.models.mfc_ft_order import MfcFtKind
from app.domains.execution.services import mfc_ft_client
from app.domains.execution.services.mfc_ft_service import (
    BankLeg,
    MfcFtError,
    SchemeLeg,
    _envelope,
    _scheme_options,
    _validate,
)
from app.domains.execution.services.mfc_masters import (
    AMC_MASTER,
    FREQUENCY_MASTER,
    amc_name,
    resolve_amc_code,
    resolve_frequency,
)


# --------------------------------------------------------------------------- masters


def test_every_transaction_kind_has_an_endpoint_and_a_family_code():
    """A kind without either is a 404 or a mis-routed envelope at run time."""
    for kind in MfcFtKind:
        assert kind in mfc_ft_client.FT_ENDPOINTS
        assert kind in mfc_ft_client.FT_OTHER_API


def test_amc_master_matches_the_published_table():
    # 43 houses, and the two code systems (letters for KFintech-serviced,
    # numerics for CAMS-serviced) share one namespace without colliding.
    assert len(AMC_MASTER) == 43
    assert amc_name("H") == "HDFC Mutual Fund"
    assert amc_name("108") == "UTI MUTUAL FUND"
    assert amc_name("nope") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The case that actually occurs: a scheme name, not a house name.
        ("HDFC Flexi Cap Fund - Regular Plan - Growth", "H"),
        ("UTI Flexi Cap Fund - Regular Plan", "108"),
        ("Tata Index Fund Nifty - Direct Plan", "T"),
        ("Nippon India Small Cap Fund", "RMF"),
        ("ICICI Prudential Bluechip Fund", "P"),
        ("Aditya Birla Sun Life Frontline Equity Fund", "B"),
        # House names, long and short.
        ("HDFC Mutual Fund", "H"),
        ("HDFC MF", "H"),
        ("Birla Sun Life MF", "B"),
        # Codes pass through untouched.
        ("H", "H"),
        ("108", "108"),
        ("rmf", "RMF"),
    ],
)
def test_amc_resolution(text, expected):
    assert resolve_amc_code(text) == expected


def test_quant_and_quantum_do_not_collide():
    """Two real houses whose names are prefixes of one another. Token equality
    keeps them apart; substring matching would not."""
    assert resolve_amc_code("Quant Small Cap Fund") == "166"
    assert resolve_amc_code("Quantum Long Term Equity Fund") == "123"


@pytest.mark.parametrize(
    "text", ["Some Fund House That Does Not Exist", "Parag Parikh Flexi Cap", "", None]
)
def test_amc_resolution_refuses_to_guess(text):
    """None is the right answer when nothing matches: the caller is then asked
    for a code, instead of an order going to the wrong fund house."""
    assert resolve_amc_code(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Monthly", "OM"),
        ("monthly", "OM"),
        ("OM", "OM"),
        ("Quarterly", "Q"),
        ("Weekly", "W"),
        ("Daily", "D"),
        ("Fortnightly", "AW"),
        ("Half-Yearly", "H"),
        ("nonsense", None),
        (None, None),
    ],
)
def test_frequency_resolution(text, expected):
    """MFC documents codes but their own samples send words, so both are taken."""
    assert resolve_frequency(text) == expected
    if expected:
        assert expected in FREQUENCY_MASTER


# --------------------------------------------------------------------------- envelope


def _env(
    kind: MfcFtKind, leg: SchemeLeg, *, mobile="+919876543210", email=None, bank=None
):
    return _envelope(
        kind=kind,
        client_ref_no="pzfttest0001",
        pan="AATPJ9485B",
        mobile=mobile,
        email=email,
        amc="H",
        scheme_options=_scheme_options(kind, leg, bank),
    )


def test_envelope_carries_the_family_code_and_one_contact():
    env = _env(MfcFtKind.REDEEM, SchemeLeg(isin="INF179K01442", units=10))
    assert env["otherAPI"] == "R"
    assert env["reqId"] == ""
    assert env["pekrn"] == ""
    assert env["mobile"] and env["email"] == ""


def test_otp_channel_agrees_with_the_populated_contact():
    """MFC rejects a mismatch with a 400 that names no field — so it is asserted
    here, where the envelope is built once for all eight families."""
    by_mobile = _env(MfcFtKind.REDEEM, SchemeLeg(isin="INF179K01442", units=1))
    block = by_mobile["data"][0]
    assert block["otpSentTo"] == "M"
    assert block["otpMobile"] and block["otpEmail"] == ""

    by_email = _env(
        MfcFtKind.REDEEM,
        SchemeLeg(isin="INF179K01442", units=1),
        mobile=None,
        email="investor@example.com",
    )
    block = by_email["data"][0]
    assert block["otpSentTo"] == "E"
    assert block["otpEmail"] and block["otpMobile"] == ""


def test_every_scheme_field_is_a_string():
    """MFC's validator rejects a JSON number where it documents String(n).

    This is a wire-format requirement, not a style preference, and it is easy to
    reintroduce by passing an amount straight through.
    """
    env = _env(
        MfcFtKind.PURCHASE,
        SchemeLeg(isin="INF179K01608", amount=25000.0, folio="17064219"),
    )
    for key, value in env["data"][0]["schemeOptions"][0].items():
        assert isinstance(value, (str, dict)), f"{key} is {type(value).__name__}"


def test_amounts_are_whole_rupees():
    """MFC's purchase docs say "No decimals allowed" outright."""
    env = _env(MfcFtKind.PURCHASE, SchemeLeg(isin="INF179K01608", amount=25000.0))
    assert env["data"][0]["schemeOptions"][0]["amount"] == "25000"


def test_a_purchase_with_a_cadence_becomes_a_sip():
    """MFC has no separate SIP endpoint — the frequency is what makes it one."""
    lumpsum = _env(MfcFtKind.PURCHASE, SchemeLeg(isin="INF179K01608", amount=1000))
    assert lumpsum["data"][0]["schemeOptions"][0]["trxnType"] == "FP"

    sip = _env(
        MfcFtKind.PURCHASE,
        SchemeLeg(
            isin="INF179K01608",
            amount=1000,
            frequency="Monthly",
            start_date="10-Oct-2026",
        ),
    )
    option = sip["data"][0]["schemeOptions"][0]
    assert option["trxnType"] == "SIP"
    assert option["frequency"] == "OM"

    additional_sip = _env(
        MfcFtKind.ADDITIONAL,
        SchemeLeg(
            isin="INF179K01608",
            amount=1000,
            frequency="Monthly",
            start_date="10-Oct-2026",
        ),
    )
    assert additional_sip["data"][0]["schemeOptions"][0]["trxnType"] == "ASIP"


def test_redemption_uses_mfcs_misspelt_key():
    """MFC's redemption schema says ``userTrxNo`` where every other family says
    ``userTrxnNo``. Correcting the spelling silently drops the value."""
    option = _scheme_options(
        MfcFtKind.REDEEM,
        SchemeLeg(isin="INF179K01442", units=5, user_trxn_no="123"),
        None,
    )[0]
    assert option["userTrxNo"] == "123"


def test_switch_and_stp_name_both_legs():
    switch = _scheme_options(
        MfcFtKind.SWITCH,
        SchemeLeg(isin="INF179K01442", to_isin="INF179K01608", amount=1000),
        None,
    )[0]
    assert switch["fromIsin"] == "INF179K01442"
    assert switch["toIsin"] == "INF179K01608"
    assert "isin" not in switch, "a switch has no single isin — MFC ignores it"

    stp = _scheme_options(
        MfcFtKind.STP,
        SchemeLeg(
            isin="INF179K01442",
            to_isin="INF179K01608",
            amount=1000,
            frequency="Monthly",
            start_date="01-Nov-2026",
        ),
        None,
    )[0]
    assert stp["trxnType"] == "STP"
    assert stp["frequency"] == "OM"
    # The instalment day is derived from the start date, which is what MFC does
    # when it is left blank.
    assert stp["stpSwpDay"] == "1"


def test_pause_and_cancel_differ_only_by_trxn_type():
    common = SchemeLeg(isin="INF179KC1HO1", user_trxn_no="37822295", folio="33557599")
    pause = _scheme_options(MfcFtKind.SIP_PAUSE, common, None)[0]
    cancel = _scheme_options(MfcFtKind.SIP_CANCEL, common, None)[0]
    assert pause["trxnType"] == "PSIP"
    assert cancel["trxnType"] == "CSIP"
    assert {k: v for k, v in pause.items() if k != "trxnType"} == {
        k: v for k, v in cancel.items() if k != "trxnType"
    }


def test_bank_block_defaults_neft_ifsc_to_the_ifsc():
    option = _scheme_options(
        MfcFtKind.REDEEM,
        SchemeLeg(isin="INF179K01442", units=1),
        BankLeg(account_no="12221150010158", ifsc="hdfc0001222"),
    )[0]
    assert option["bank"]["ifsc"] == "HDFC0001222"
    assert option["bank"]["neftIfsc"] == "HDFC0001222"
    assert option["bank"]["default"] == "Y"


# --------------------------------------------------------------------------- guards


@pytest.mark.parametrize(
    ("kind", "leg", "needle"),
    [
        (MfcFtKind.REDEEM, SchemeLeg(isin="INF179K01442"), "amount"),
        (MfcFtKind.SWITCH, SchemeLeg(isin="INF179K01442", amount=1), "switch INTO"),
        (MfcFtKind.PURCHASE, SchemeLeg(isin="INF179K01608"), "amount"),
        (
            MfcFtKind.PURCHASE,
            SchemeLeg(isin="NOT-AN-ISIN", amount=1),
            "not a valid ISIN",
        ),
        (MfcFtKind.PURCHASE, SchemeLeg(amount=1), "ISIN is required"),
        (MfcFtKind.SIP_PAUSE, SchemeLeg(isin="INF179KC1HO1"), "transaction number"),
        (
            MfcFtKind.STP,
            SchemeLeg(
                isin="INF179K01442",
                to_isin="INF179K01608",
                amount=1,
                frequency="never",
                start_date="01-Nov-2026",
            ),
            "cadence",
        ),
        (
            MfcFtKind.SWP,
            SchemeLeg(isin="INF179K01442", amount=1, frequency="Monthly"),
            "start date",
        ),
    ],
)
def test_guards_reject_before_anything_reaches_mfc(kind, leg, needle):
    """Every one of these would otherwise be a 400 from MFC AFTER the order
    exists on their side, worded so it names a field but not the rule."""
    with pytest.raises(MfcFtError, match=needle):
        _validate(kind, leg)


def test_a_switch_into_the_same_scheme_is_refused():
    with pytest.raises(MfcFtError, match="same"):
        _validate(
            MfcFtKind.SWITCH,
            SchemeLeg(isin="INF179K01442", to_isin="INF179K01442", amount=1000),
        )


def test_redeeming_all_units_needs_no_amount():
    _validate(MfcFtKind.REDEEM, SchemeLeg(isin="INF179K01442", all_units=True))


# --------------------------------------------------------------------------- responses


def test_per_scheme_rejections_are_found_in_both_registrar_shapes():
    """KFintech answers ``error: [...]`` and CAMS ``errors: [...]``, and both can
    ride an HTTP 200. Reading only the status code reports a rejected order as
    placed."""
    kfin = {"success": [], "error": [{"responseMessage": "Folio not found"}]}
    cams = {"errors": [{"message": "Scheme not available", "code": "400"}]}

    assert mfc_ft_client.extract_errors(kfin)[0]["responseMessage"] == "Folio not found"
    assert mfc_ft_client.extract_errors(cams)[0]["message"] == "Scheme not available"
    assert mfc_ft_client.extract_errors({"success": [{"ok": 1}]}) == []
    assert mfc_ft_client.extract_errors(None) == []


@pytest.mark.parametrize(
    ("rta_text", "expected"),
    [
        ("Transaction Successful", "success"),
        ("Units allotted", "success"),
        ("Transaction rejected", "rejected"),
        ("REJECTED", "rejected"),
        ("Transaction under process", "processing"),
        ("Pending for consent", "processing"),
        ("Failed at RTA", "failed"),
        ("something nobody documented", "processing"),
    ],
)
def test_registrar_status_maps_to_ours(rta_text, expected):
    """Neither registrar publishes a closed list of phrasings, so the mapping is
    substring-based and lossy — which is why the original is also stored."""
    from app.domains.execution.services.mfc_ft_service import _map_rta_status

    assert _map_rta_status(rta_text).value == expected


def test_status_is_read_from_the_nested_row_not_the_envelope():
    """CAMS nests the per-scheme detail under ``errors`` even for successes."""
    from app.domains.execution.services.mfc_ft_service import _read_status

    status, message, trxn = _read_status(
        {
            "reqId": "1",
            "errors": [
                {
                    "transactionStatus": "Transaction Successful",
                    "message": "Order accepted",
                    "userTrxnNo": "MOCK1",
                }
            ],
        }
    )
    assert (status, message, trxn) == (
        "Transaction Successful",
        "Order accepted",
        "MOCK1",
    )


def test_consent_req_id_is_sent_as_the_type_it_actually_is():
    """MFC's samples send reqId as a bare number on the consent and status legs,
    but their hyphenated ids cannot be numbers at all."""
    from app.domains.execution.services.mfc_ft_client import _as_number_if_possible

    assert _as_number_if_possible("85938") == 85938
    assert _as_number_if_possible("2559654-170718497") == "2559654-170718497"
