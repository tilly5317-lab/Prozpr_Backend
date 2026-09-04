"""MF Central code masters, transcribed from their FT API documentation.

MFC's payloads are built out of short codes, not names: ``amc: "H"``,
``frequency: "OM"``, ``otherAPI: "R"``. Every one of these is a closed set MFC
publishes as a table in the FT docs and validates server-side, so a wrong code
is a 400 with a message that names the field but not the allowed values.

They live here as data rather than as free strings at the call sites because:

* the AMC code is the one field we cannot derive from a holding. Our ledger
  carries AMC *names* (from the CAS), and the FT APIs want MFC's code — so
  every order needs this lookup, and getting it wrong is unrecoverable at the
  point of submission;
* the codes are shared across all eight transaction types, and duplicating them
  per service would guarantee drift the first time MFC onboards a new AMC.

Transcribed from the AMC / frequency / Other-API / account-type masters in
``4. Redemption.docx`` and ``8.STP.docx`` (they are identical across the set).

NOTE the two code systems in the AMC table: single letters and short strings
(360 ONE = "IF", HDFC = "H") for KFintech-serviced houses, numerics ("128",
"108") for CAMS-serviced ones. They are one namespace and do not collide — and
they match the ``amc`` field that comes back on a CAS, which is what makes the
inbound and outbound halves of the integration line up.
"""

from __future__ import annotations

import re
from typing import Optional

# --------------------------------------------------------------------------- AMCs

# code -> (full name, short name). Order preserved from MFC's table.
AMC_MASTER: dict[str, tuple[str, str]] = {
    "IF": ("360 ONE Mutual Fund", "360 ONE MF"),
    "B": ("Aditya Birla Sun Life Mutual Fund", "BIRLA MF"),
    "G": ("Bandhan Mutual Fund", "Bandhan MF"),
    "D": ("DSP Mutual Fund", "DSP MF"),
    "FTI": ("Franklin Templeton Mutual Fund", "Franklin Templeton MF"),
    "H": ("HDFC Mutual Fund", "HDFC MF"),
    "O": ("HSBC Mutual Fund", "HSBC MF"),
    "P": ("ICICI Prudential Mutual Fund", "I-PRU MF"),
    "K": ("Kotak Mutual Fund", "KOTAK MF"),
    "MM": ("Mahindra Manulife Mutual Fund", "MAHINDRA MF"),
    "PLF": ("NAVI Mutual Fund", "NAVI MF"),
    "PP": ("PPFAS Mutual Fund", "PPFAS MF"),
    "L": ("SBI Mutual Fund", "SBI MF"),
    "SH": ("Shriram Mutual Fund", "SHRIRAM MF"),
    "T": ("Tata Mutual Fund", "TATA MF"),
    "UK": ("Union Mutual Fund", "UNION MF"),
    "Y": ("WhiteOak Capital Mutual Fund", "WhiteOak Capital MF"),
    "128": ("AXIS MUTUAL FUND", "Axis MF"),
    "178": ("BARODA BNP PARIBAS MUTUAL FUND", "Baroda MF"),
    "116": ("BOI AXA MUTUAL FUND", "Boi MF"),
    "189": ("Bajaj Finserv Mutual Fund", "Bajaj Finserv"),
    "101": ("CANARA ROBECO MUTUAL FUND", "Canara MF"),
    "118": ("EDELWEISS MUTUAL FUND", "Edelweiss MF"),
    "125": ("Groww Mutual Fund", "Groww MF"),
    "120": ("INVESCO MUTUAL FUND", "Invesco MF"),
    "152": ("ITI MUTUAL FUND", "Iti MF"),
    "105": ("JM FINANCIAL MUTUAL FUND", "Jm MF"),
    "102": ("LIC MUTUAL FUND", "Lic MF"),
    "117": ("MIRAE ASSET MUTUAL FUND", "Mirae MF"),
    "127": ("MOTILAL OSWAL MUTUAL FUND", "Motilal MF"),
    "RMF": ("NIPPON INDIA MUTUAL FUND", "Nippon MF"),
    "187": ("NJ MUTUAL FUND", "Nj MF"),
    "129": ("PGIM INDIA MUTUAL FUND", "Pgim MF"),
    "166": ("QUANT MUTUAL FUND", "Quant MF"),
    "123": ("QUANTUM MUTUAL FUND", "Quantum MF"),
    "188": ("SAMCO MUTUAL FUND", "Samco MF"),
    "176": ("SUNDARAM MUTUAL FUND", "Sundaram MF"),
    "104": ("TAURUS MUTUAL FUND", "Taurus MF"),
    "185": ("TRUST MUTUAL FUND", "Trust MF"),
    "108": ("UTI MUTUAL FUND", "UTI MF"),
    "139": ("OLD BRIDGE MUTUAL FUND", "Old Bridge MF"),
    "HLS": ("HELIOS MUTUAL FUND", "Helios MF"),
    "Z": ("ZERODHA MUTUAL FUND", "Zerodha MF"),
}

# Words that carry no identity, stripped before matching a fund-house name.
# "Aditya Birla Sun Life Mutual Fund" and "BIRLA MF" have to reduce to
# overlapping token sets, and "Mutual"/"Fund" are in every single name.
_NOISE = {
    "mutual",
    "fund",
    "mf",
    "asset",
    "management",
    "amc",
    "india",
    "ltd",
    "limited",
    "capital",
    "financial",
    "the",
    "of",
    "co",
    "company",
}


def _tokens(name: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", name.lower()) if t and t not in _NOISE}


def amc_name(code: Optional[str]) -> Optional[str]:
    """Full fund-house name for an MFC AMC code."""
    entry = AMC_MASTER.get((code or "").strip().upper())
    return entry[0] if entry else None


def resolve_amc_code(name_or_code: Optional[str]) -> Optional[str]:
    """Best-effort AMC code from a code, a fund-house name, or a SCHEME name.

    The scheme-name case is the one that matters in practice: our holdings carry
    "HDFC Flexi Cap Fund - Regular Plan", not "HDFC Mutual Fund", and the fund
    house is a prefix of the scheme rather than the whole of it. So containment
    is tried before similarity — an AMC whose distinctive words all appear in
    the input is a match no matter how much else the input says.

    Ties are broken by specificity, so "Aditya Birla Sun Life Frontline Equity"
    resolves to Birla rather than to any house sharing one incidental word.

    Returns None rather than guessing. An order built on a wrong AMC is not a
    near miss — it is submitted against a different fund house — so a
    confident-but-wrong answer here is worse than making the caller be explicit.
    """
    text = (name_or_code or "").strip()
    if not text:
        return None
    if text.upper() in AMC_MASTER:
        return text.upper()

    wanted = _tokens(text)
    if not wanted:
        return None

    # Pass 1 — containment. Longest (most specific) AMC name wins.
    contained: tuple[int, Optional[str]] = (0, None)
    for code, (full, short) in AMC_MASTER.items():
        for candidate in (full, short):
            have = _tokens(candidate)
            if have and have <= wanted and len(have) > contained[0]:
                contained = (len(have), code)
    if contained[1]:
        return contained[1]

    # Pass 2 — similarity, for names that are close but not contained
    # ("Birla Sun Life MF" against "Aditya Birla Sun Life Mutual Fund").
    best: tuple[float, Optional[str]] = (0.0, None)
    for code, (full, short) in AMC_MASTER.items():
        for candidate in (full, short):
            have = _tokens(candidate)
            if not have:
                continue
            overlap = len(wanted & have)
            if not overlap:
                continue
            # Jaccard rather than raw overlap, so a house that happens to share
            # a common word with many names does not win on volume alone.
            score = overlap / len(wanted | have)
            if score > best[0]:
                best = (score, code)

    return best[1] if best[0] >= 0.5 else None


# --------------------------------------------------------------------------- frequency

FREQUENCY_MASTER: dict[str, str] = {
    "Y": "Yearly",
    "H": "Half Yearly",
    "Q": "Quarterly",
    "OTM": "Once in Two Months",
    "OM": "Once in a Month",
    "TM": "Twice a Month",
    "THM": "Thrice a Month",
    "SM": "Specific dates in a Month",
    "BZ": "Business Day",
    "D": "Daily",
    "DZ": "Daily ZIP",
    "O": "One Shot",
    "FM": "Four in a Month",
    "OW": "Once a Week",
    "WD": "Weekly Dates",
    "AW": "Alternate Week",
    "W": "Weekly",
}

# What a human is likely to send. MFC's own samples pass the WORD ("Monthly")
# in the STP/SWP payloads while documenting the code, so both are accepted and
# normalised — their validator has been observed taking either.
_FREQUENCY_ALIASES: dict[str, str] = {
    "MONTHLY": "OM",
    "MONTH": "OM",
    "ONCEAMONTH": "OM",
    "QUARTERLY": "Q",
    "QUARTER": "Q",
    "YEARLY": "Y",
    "ANNUAL": "Y",
    "ANNUALLY": "Y",
    "HALFYEARLY": "H",
    "SEMIANNUAL": "H",
    "WEEKLY": "W",
    "WEEK": "W",
    "DAILY": "D",
    "DAY": "D",
    "FORTNIGHTLY": "AW",
    "BIWEEKLY": "AW",
}


def resolve_frequency(value: Optional[str]) -> Optional[str]:
    """Normalise a SIP/STP/SWP cadence to MFC's code."""
    text = (value or "").strip()
    if not text:
        return None
    if text.upper() in FREQUENCY_MASTER:
        return text.upper()
    return _FREQUENCY_ALIASES.get(re.sub(r"[^A-Za-z]", "", text).upper())


# --------------------------------------------------------------------------- other

# MFC's "Other API" master — the value of the top-level ``otherAPI`` field,
# which tells them which transaction family the envelope carries.
OTHER_API_MASTER: dict[str, str] = {
    "R": "Redemption",
    "S": "Switch",
    "NP": "New Purchase (Lumpsum / SIP)",
    "AP": "Additional Purchase (Lumpsum / SIP)",
    "ST": "STP",
    "SW": "SWP",
    "SS": "Summary (SIP / STP / SWP)",
    "SPC": "SIP Pause and Cancel — submit",
    "SPCV": "SIP Pause and Cancel — validate",
}

ACCOUNT_TYPE_MASTER: dict[str, str] = {
    "SB": "Savings Account",
    "CA": "Current Account",
}

# ``otpSentTo``: which channel MFC should send the transaction OTP down. Not a
# preference — it must agree with which of otpMobile/otpEmail is populated.
OTP_CHANNEL_MASTER: dict[str, str] = {"M": "Mobile", "E": "Email"}

# The per-scheme ``trxnType``. Distinct from ``otherAPI``: that names the
# envelope, this names the line item, and for purchases they disagree
# (otherAPI "NP" carries trxnType "FP" or "SIP").
TRXN_TYPE_MASTER: dict[str, str] = {
    "FP": "Fresh Purchase",
    "AP": "Additional Purchase",
    "SIP": "SIP registration",
    "ASIP": "Additional SIP registration",
    "R": "Redemption",
    "SW": "Switch",
    "STP": "STP registration",
    "SWP": "SWP registration",
    "PSIP": "Pause SIP",
    "CSIP": "Cancel SIP",
}
