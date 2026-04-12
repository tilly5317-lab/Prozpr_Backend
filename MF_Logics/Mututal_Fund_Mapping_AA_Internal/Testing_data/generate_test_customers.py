#!/usr/bin/env python3
"""
Generate 5 realistic customer AA MF holdings JSON files using real
mutual fund data from latest_nav_active.csv.

Each file mirrors the Account Aggregator (CAMS/KFintech) format used by
split_aa_mf_holdings.py, with correct ISINs, scheme names, and NAVs
so downstream mapping logic works.

Usage:
    python generate_test_customers.py
"""
import json
import random
from pathlib import Path
from datetime import datetime, timedelta

OUTPUT_DIR = Path(__file__).parent

# ---------------------------------------------------------------------------
# Real fund catalogue – sourced from latest_nav_active.csv
# Fields: isin, schemeName, fundHouse, nav, amc_code, rta, asset_type, sub_category
# ---------------------------------------------------------------------------
FUND_CATALOGUE = [
    # ── HDFC (amc="H", rta=CAMS) ──
    {"isin": "INF179KB1HK0", "schemeName": "HDFC Liquid Fund - Growth Plan",
     "fundHouse": "HDFC Mutual Fund", "nav": 5360.7045, "amc": "H", "rta": "CAMS",
     "assetType": "CASH", "sub_category": "Liquid Fund"},
    {"isin": "INF179KB1HS3", "schemeName": "HDFC Overnight Fund - Growth Option",
     "fundHouse": "HDFC Mutual Fund", "nav": 3954.7187, "amc": "H", "rta": "CAMS",
     "assetType": "CASH", "sub_category": "Overnight Fund"},
    {"isin": "INF179K01442", "schemeName": "HDFC Low Duration Fund - Growth",
     "fundHouse": "HDFC Mutual Fund", "nav": 60.1509, "amc": "H", "rta": "CAMS",
     "assetType": "DEBT", "sub_category": "Low Duration Fund"},
    {"isin": "INF179K01CU6", "schemeName": "HDFC Short Term Debt Fund - Growth Option",
     "fundHouse": "HDFC Mutual Fund", "nav": 33.1716, "amc": "H", "rta": "CAMS",
     "assetType": "DEBT", "sub_category": "Short Duration Fund"},
    {"isin": "INF179K01913", "schemeName": "HDFC Medium Term Debt Fund - Growth Option",
     "fundHouse": "HDFC Mutual Fund", "nav": 58.1709, "amc": "H", "rta": "CAMS",
     "assetType": "DEBT", "sub_category": "Medium Duration Fund"},
    {"isin": "INF179K01DC2", "schemeName": "HDFC Corporate Bond Fund - Growth Option",
     "fundHouse": "HDFC Mutual Fund", "nav": 33.2725, "amc": "H", "rta": "CAMS",
     "assetType": "DEBT", "sub_category": "Corporate Bond Fund"},
    {"isin": "INF179K01848", "schemeName": "HDFC Dynamic Debt Fund - Growth Option",
     "fundHouse": "HDFC Mutual Fund", "nav": 89.1517, "amc": "H", "rta": "CAMS",
     "assetType": "DEBT", "sub_category": "Dynamic Bond"},
    {"isin": "INF179K01608", "schemeName": "HDFC Flexi Cap Fund - Growth Plan",
     "fundHouse": "HDFC Mutual Fund", "nav": 1848.132, "amc": "H", "rta": "CAMS",
     "assetType": "EQUITY", "sub_category": "Flexi Cap Fund"},
    {"isin": "INF179K01CR2", "schemeName": "HDFC Mid Cap Fund - Growth Plan",
     "fundHouse": "HDFC Mutual Fund", "nav": 182.827, "amc": "H", "rta": "CAMS",
     "assetType": "EQUITY", "sub_category": "Mid Cap Fund"},
    {"isin": "INF179K01BE2", "schemeName": "HDFC Large Cap Fund - Growth Option - Regular Plan",
     "fundHouse": "HDFC Mutual Fund", "nav": 1030.882, "amc": "H", "rta": "CAMS",
     "assetType": "EQUITY", "sub_category": "Large Cap Fund"},
    {"isin": "INF179K01830", "schemeName": "HDFC Balanced Advantage Fund - Growth Plan",
     "fundHouse": "HDFC Mutual Fund", "nav": 489.247, "amc": "H", "rta": "CAMS",
     "assetType": "EQUITY", "sub_category": "Dynamic Asset Allocation or Balanced Advantage"},
    {"isin": "INF179K01BB8", "schemeName": "HDFC ELSS Tax saver - Growth Plan",
     "fundHouse": "HDFC Mutual Fund", "nav": 1271.954, "amc": "H", "rta": "CAMS",
     "assetType": "EQUITY", "sub_category": "ELSS"},
    {"isin": "INF179K01KZ8", "schemeName": "HDFC Nifty 50 Index Fund - Growth Plan",
     "fundHouse": "HDFC Mutual Fund", "nav": 215.7764, "amc": "H", "rta": "CAMS",
     "assetType": "EQUITY", "sub_category": "Index Funds"},
    {"isin": "INF179K01LA9", "schemeName": "HDFC BSE Sensex Index Fund - Growth Plan",
     "fundHouse": "HDFC Mutual Fund", "nav": 679.881, "amc": "H", "rta": "CAMS",
     "assetType": "EQUITY", "sub_category": "Index Funds"},
    {"isin": "INF179K01LC5", "schemeName": "HDFC Gold ETF Fund of Fund - Growth Option",
     "fundHouse": "HDFC Mutual Fund", "nav": 43.378, "amc": "H", "rta": "CAMS",
     "assetType": "EQUITY", "sub_category": "FoF Domestic"},
    {"isin": "INF179K01426", "schemeName": "HDFC Value Fund - Growth Plan",
     "fundHouse": "HDFC Mutual Fund", "nav": 683.951, "amc": "H", "rta": "CAMS",
     "assetType": "EQUITY", "sub_category": "Value Fund"},

    # ── SBI (amc="L", rta=CAMS) ──
    {"isin": "INF200K01MA1", "schemeName": "SBI Liquid Fund - Regular Plan - Growth",
     "fundHouse": "SBI Mutual Fund", "nav": 4268.8587, "amc": "L", "rta": "CAMS",
     "assetType": "CASH", "sub_category": "Liquid Fund"},
    {"isin": "INF200K01U41", "schemeName": "SBI Banking & PSU Fund - Regular Plan - Growth",
     "fundHouse": "SBI Mutual Fund", "nav": 3204.5427, "amc": "L", "rta": "CAMS",
     "assetType": "DEBT", "sub_category": "Banking and PSU Fund"},
    {"isin": "INF200K01T28", "schemeName": "SBI Small Cap Fund - Regular Plan - Growth",
     "fundHouse": "SBI Mutual Fund", "nav": 151.0376, "amc": "L", "rta": "CAMS",
     "assetType": "EQUITY", "sub_category": "Small Cap Fund"},
    {"isin": "INF200K01222", "schemeName": "SBI Flexicap Fund - Regular Plan - Growth Option",
     "fundHouse": "SBI Mutual Fund", "nav": 98.8697, "amc": "L", "rta": "CAMS",
     "assetType": "EQUITY", "sub_category": "Flexi Cap Fund"},
    {"isin": "INF200K01305", "schemeName": "SBI Large & Midcap Fund - Regular Plan - Growth",
     "fundHouse": "SBI Mutual Fund", "nav": 585.7466, "amc": "L", "rta": "CAMS",
     "assetType": "EQUITY", "sub_category": "Large & Mid Cap Fund"},
    {"isin": "INF200K01107", "schemeName": "SBI Equity Hybrid Fund - Regular Plan - Growth",
     "fundHouse": "SBI Mutual Fund", "nav": 286.4497, "amc": "L", "rta": "CAMS",
     "assetType": "EQUITY", "sub_category": "Aggressive Hybrid Fund"},

    # ── ICICI Prudential (amc="P", rta=CAMS) ──
    {"isin": "INF109K01VQ1", "schemeName": "ICICI Prudential Liquid Fund - Growth",
     "fundHouse": "ICICI Prudential Mutual Fund", "nav": 404.322, "amc": "P", "rta": "CAMS",
     "assetType": "CASH", "sub_category": "Liquid Fund"},
    {"isin": "INF109K01TX1", "schemeName": "ICICI Prudential Money Market Fund Option - Growth",
     "fundHouse": "ICICI Prudential Mutual Fund", "nav": 396.9075, "amc": "P", "rta": "CAMS",
     "assetType": "DEBT", "sub_category": "Money Market Fund"},
    {"isin": "INF109K01654", "schemeName": "ICICI Prudential Short Term Fund - Growth Option",
     "fundHouse": "ICICI Prudential Mutual Fund", "nav": 62.4, "amc": "P", "rta": "CAMS",
     "assetType": "DEBT", "sub_category": "Short Duration Fund"},
    {"isin": "INF109K01431", "schemeName": "ICICI Prudential Large & Mid Cap Fund - Growth",
     "fundHouse": "ICICI Prudential Mutual Fund", "nav": 939.97, "amc": "P", "rta": "CAMS",
     "assetType": "EQUITY", "sub_category": "Large & Mid Cap Fund"},
    {"isin": "INF109K01BL4", "schemeName": "ICICI Prudential Large Cap Fund - Growth",
     "fundHouse": "ICICI Prudential Mutual Fund", "nav": 101.19, "amc": "P", "rta": "CAMS",
     "assetType": "EQUITY", "sub_category": "Large Cap Fund"},
    {"isin": "INF109K01BI0", "schemeName": "ICICI Prudential Smallcap Fund - Growth",
     "fundHouse": "ICICI Prudential Mutual Fund", "nav": 77.22, "amc": "P", "rta": "CAMS",
     "assetType": "EQUITY", "sub_category": "Small Cap Fund"},
    {"isin": "INF109K01480", "schemeName": "ICICI Prudential Equity & Debt Fund - Growth",
     "fundHouse": "ICICI Prudential Mutual Fund", "nav": 380.0, "amc": "P", "rta": "CAMS",
     "assetType": "EQUITY", "sub_category": "Aggressive Hybrid Fund"},
    {"isin": "INF109K01506", "schemeName": "ICICI Prudential Technology Fund - Growth",
     "fundHouse": "ICICI Prudential Mutual Fund", "nav": 167.62, "amc": "P", "rta": "CAMS",
     "assetType": "EQUITY", "sub_category": "Sectoral/ Thematic"},
    {"isin": "INF109K01AV5", "schemeName": "ICICI Prudential Infrastructure Fund - Growth",
     "fundHouse": "ICICI Prudential Mutual Fund", "nav": 181.6, "amc": "P", "rta": "CAMS",
     "assetType": "EQUITY", "sub_category": "Sectoral/ Thematic"},
    {"isin": "INF109K01BU5", "schemeName": "ICICI Prudential Banking and Financial Services Fund - Growth",
     "fundHouse": "ICICI Prudential Mutual Fund", "nav": 118.91, "amc": "P", "rta": "CAMS",
     "assetType": "EQUITY", "sub_category": "Sectoral/ Thematic"},
    {"isin": "INF109K01AF8", "schemeName": "ICICI Prudential Value Fund - Growth",
     "fundHouse": "ICICI Prudential Mutual Fund", "nav": 440.64, "amc": "P", "rta": "CAMS",
     "assetType": "EQUITY", "sub_category": "Value Fund"},

    # ── Axis (amc="128", rta=KFINTECH) ──
    {"isin": "INF846K01412", "schemeName": "Axis Liquid Fund - Regular Plan - Growth Option",
     "fundHouse": "Axis Mutual Fund", "nav": 3043.0761, "amc": "128", "rta": "KFINTECH",
     "assetType": "CASH", "sub_category": "Liquid Fund"},
    {"isin": "INF846K01644", "schemeName": "Axis Short Duration Fund - Regular Plan - Growth Option",
     "fundHouse": "Axis Mutual Fund", "nav": 32.0075, "amc": "128", "rta": "KFINTECH",
     "assetType": "DEBT", "sub_category": "Short Duration Fund"},
    {"isin": "INF846K01131", "schemeName": "Axis ELSS Tax Saver Fund - Regular Plan - Growth",
     "fundHouse": "Axis Mutual Fund", "nav": 85.9079, "amc": "128", "rta": "KFINTECH",
     "assetType": "EQUITY", "sub_category": "ELSS"},
    {"isin": "INF846K01859", "schemeName": "Axis Midcap Fund - Regular Plan - Growth",
     "fundHouse": "Axis Mutual Fund", "nav": 103.98, "amc": "128", "rta": "KFINTECH",
     "assetType": "EQUITY", "sub_category": "Mid Cap Fund"},
    {"isin": "INF846K01164", "schemeName": "Axis Large Cap Fund - Regular Plan - Growth",
     "fundHouse": "Axis Mutual Fund", "nav": 54.54, "amc": "128", "rta": "KFINTECH",
     "assetType": "EQUITY", "sub_category": "Large Cap Fund"},

    # ── Others ──
    {"isin": "INF090I01775", "schemeName": "Franklin India ELSS Tax Saver Fund - Growth",
     "fundHouse": "Franklin Templeton Mutual Fund", "nav": 1317.8032, "amc": "F", "rta": "CAMS",
     "assetType": "EQUITY", "sub_category": "ELSS"},
    {"isin": "INF789F01JN2", "schemeName": "UTI Nifty 50 Index Fund - Regular Plan - Growth Option",
     "fundHouse": "UTI Mutual Fund", "nav": 156.4431, "amc": "UT", "rta": "KFINTECH",
     "assetType": "EQUITY", "sub_category": "Index Funds"},
    {"isin": "INF209K01322", "schemeName": "Aditya Birla Sun Life MNC Fund - Growth - Regular Plan",
     "fundHouse": "Aditya Birla Sun Life Mutual Fund", "nav": 1168.88, "amc": "B", "rta": "CAMS",
     "assetType": "EQUITY", "sub_category": "Sectoral/ Thematic"},
    {"isin": "INF204K01UN9", "schemeName": "Nippon India Liquid Fund - Growth Plan",
     "fundHouse": "Nippon India Mutual Fund", "nav": 6666.6939, "amc": "R", "rta": "KFINTECH",
     "assetType": "CASH", "sub_category": "Liquid Fund"},
    {"isin": "INF209K01165", "schemeName": "Aditya Birla Sun Life Large & Mid Cap Fund - Regular Growth",
     "fundHouse": "Aditya Birla Sun Life Mutual Fund", "nav": 825.66, "amc": "B", "rta": "CAMS",
     "assetType": "EQUITY", "sub_category": "Large & Mid Cap Fund"},
]

FUND_BY_ISIN = {f["isin"]: f for f in FUND_CATALOGUE}


# ---------------------------------------------------------------------------
# Customer profiles
# ---------------------------------------------------------------------------
CUSTOMERS = [
    {
        "pan": "BKRPS4521M",
        "email": "rajesh.sharma@example.com",
        "mobile": "9876543210",
        "investor": {
            "investorFirstName": "RAJESH",
            "investorMiddleName": "KUMAR",
            "investorLastName": "SHARMA",
        },
        "address": {
            "address1": "42 PARK STREET SALT LAKE SECTOR V",
            "address2": "", "address3": "",
            "city": "KOLKATA", "district": "KOLKATA",
            "state": "West Bengal", "pincode": "700091", "country": "India",
        },
        "folio_prefix": "2024",
        "holdings": [
            {"isin": "INF179KB1HK0", "units": 186.250, "cost": 950000},
            {"isin": "INF179K01CU6", "units": 15100.500, "cost": 475000},
            {"isin": "INF200K01U41", "units": 156.200, "cost": 480000},
            {"isin": "INF109K01TX1", "units": 1260.750, "cost": 470000},
            {"isin": "INF846K01644", "units": 15620.350, "cost": 480000},
            {"isin": "INF179K01DC2", "units": 9010.400, "cost": 285000},
        ],
    },
    {
        "pan": "CMNPM7823K",
        "email": "priya.menon@example.com",
        "mobile": "9123456780",
        "investor": {
            "investorFirstName": "PRIYA",
            "investorMiddleName": "SUNDAR",
            "investorLastName": "MENON",
        },
        "address": {
            "address1": "FLAT 12B PRESTIGE TOWER MG ROAD",
            "address2": "NEAR METRO STATION", "address3": "",
            "city": "BANGALORE", "district": "BANGALORE URBAN",
            "state": "Karnataka", "pincode": "560001", "country": "India",
        },
        "folio_prefix": "3051",
        "holdings": [
            {"isin": "INF179K01608", "units": 540.320, "cost": 850000},
            {"isin": "INF109K01431", "units": 530.100, "cost": 450000},
            {"isin": "INF200K01T28", "units": 3300.850, "cost": 400000},
            {"isin": "INF846K01859", "units": 4810.200, "cost": 420000},
            {"isin": "INF179K01CR2", "units": 2740.600, "cost": 425000},
            {"isin": "INF179K01BB8", "units": 78.550, "cost": 85000},
        ],
    },
    {
        "pan": "DRTPA6190L",
        "email": "amit.rathore@example.com",
        "mobile": "9988776655",
        "investor": {
            "investorFirstName": "AMIT",
            "investorMiddleName": "SINGH",
            "investorLastName": "RATHORE",
        },
        "address": {
            "address1": "A-15 VASANT VIHAR",
            "address2": "NEAR DPS SCHOOL", "address3": "",
            "city": "NEW DELHI", "district": "SOUTH WEST DELHI",
            "state": "Delhi", "pincode": "110057", "country": "India",
        },
        "folio_prefix": "4087",
        "holdings": [
            {"isin": "INF179K01830", "units": 1020.600, "cost": 450000},
            {"isin": "INF109K01480", "units": 1315.780, "cost": 450000},
            {"isin": "INF200K01222", "units": 5060.300, "cost": 450000},
            {"isin": "INF179K01DC2", "units": 15030.200, "cost": 475000},
            {"isin": "INF846K01131", "units": 5820.750, "cost": 450000},
            {"isin": "INF200K01MA1", "units": 117.350, "cost": 475000},
            {"isin": "INF200K01107", "units": 1745.900, "cost": 450000},
        ],
    },
    {
        "pan": "FLKPD2847N",
        "email": "deepa.patel@example.com",
        "mobile": "9871234567",
        "investor": {
            "investorFirstName": "DEEPA",
            "investorMiddleName": None,
            "investorLastName": "PATEL",
        },
        "address": {
            "address1": "B-302 SHREEJI HEIGHTS CG ROAD",
            "address2": "NAVRANGPURA", "address3": "",
            "city": "AHMEDABAD", "district": "AHMEDABAD",
            "state": "Gujarat", "pincode": "380009", "country": "India",
        },
        "folio_prefix": "5163",
        "holdings": [
            {"isin": "INF179K01KZ8", "units": 2320.450, "cost": 450000},
            {"isin": "INF789F01JN2", "units": 3200.100, "cost": 450000},
            {"isin": "INF179K01LA9", "units": 735.800, "cost": 450000},
            {"isin": "INF179K01LC5", "units": 11540.200, "cost": 430000},
            {"isin": "INF109K01BL4", "units": 4940.700, "cost": 450000},
            {"isin": "INF090I01775", "units": 37.950, "cost": 45000},
        ],
    },
    {
        "pan": "GHJVK9354R",
        "email": "vikram.joshi@example.com",
        "mobile": "9765432109",
        "investor": {
            "investorFirstName": "VIKRAM",
            "investorMiddleName": None,
            "investorLastName": "JOSHI",
        },
        "address": {
            "address1": "201 RAHEJA CLASSIQUE ANDHERI LINK ROAD",
            "address2": "ANDHERI WEST", "address3": "",
            "city": "MUMBAI", "district": "MUMBAI SUBURBAN",
            "state": "Maharashtra", "pincode": "400053", "country": "India",
        },
        "folio_prefix": "6290",
        "holdings": [
            {"isin": "INF109K01506", "units": 2980.450, "cost": 425000},
            {"isin": "INF109K01AV5", "units": 2750.300, "cost": 425000},
            {"isin": "INF109K01BU5", "units": 4210.600, "cost": 425000},
            {"isin": "INF209K01322", "units": 42.780, "cost": 42000},
            {"isin": "INF204K01UN9", "units": 75.020, "cost": 475000},
            {"isin": "INF209K01165", "units": 605.350, "cost": 425000},
            {"isin": "INF109K01AF8", "units": 1135.200, "cost": 450000},
        ],
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
TXN_DESCRIPTIONS = {
    "P":   "Purchase",
    "SIP": "Systematic Investment",
    "SI":  "Switch-In",
    "R":   "Redemption",
    "SO":  "Switch Out",
}

BROKER_LIST = [
    ("ARN-0155", "NJ IndiaInvest Pvt Ltd"),
    ("ARN-1308", "Almondz Global Securities Ltd"),
    ("ARN-5780", "Prasun Chakrabarti"),
    ("DIRECT", "Direct"),
    ("PCASON", "Prudent Corporate Advisory Services Ltd"),
    ("ARN-0010", "Bajaj Capital Ltd."),
    ("ARN-81760", "Abhishek Jain"),
]

random.seed(42)


def _fmt_date(dt: datetime) -> str:
    return dt.strftime("%d-%b-%Y").upper()


def _fmt_nav(nav: float, decimals: int = 4) -> str:
    return f"{nav:.{decimals}f}"


def _generate_transactions(holdings, folio, customer_email, start_year=2020):
    """Build a realistic transaction history for each holding."""
    txns = []
    for h in holdings:
        fund = FUND_BY_ISIN[h["isin"]]
        units_remaining = h["units"]
        cost = h["cost"]

        n_purchases = random.randint(2, 5)
        unit_per_purchase = units_remaining / n_purchases
        base_date = datetime(start_year, random.randint(1, 6), random.randint(1, 28))

        purchase_nav = cost / units_remaining
        nav_drift = fund["nav"] / purchase_nav if purchase_nav > 0 else 1.0

        for i in range(n_purchases):
            txn_date = base_date + timedelta(days=random.randint(30, 180) * (i + 1))
            if txn_date > datetime(2025, 12, 31):
                txn_date = datetime(2025, random.randint(1, 6), random.randint(1, 28))

            step_nav = purchase_nav * (1 + (nav_drift - 1) * (i / max(n_purchases, 1)))
            u = round(unit_per_purchase * random.uniform(0.8, 1.2), 3)
            amt = round(u * step_nav, 2)

            txn_type = "SIP" if i > 0 and random.random() < 0.5 else "P"
            txns.append({
                "email": customer_email,
                "amc": fund["amc"],
                "amcName": fund["fundHouse"],
                "folio": folio,
                "checkDigit": str(random.randint(10, 99)),
                "postedDate": _fmt_date(txn_date),
                "scheme": h["isin"][-5:],
                "schemeName": fund["schemeName"],
                "purchasePrice": _fmt_nav(step_nav),
                "sttTax": "0.00",
                "tax": "0.00",
                "totalTax": "0.00",
                "stampDuty": "",
                "isin": h["isin"],
                "trxnDate": _fmt_date(txn_date),
                "trxnDesc": TXN_DESCRIPTIONS[txn_type],
                "trxnAmount": f"{amt:.2f}",
                "trxnUnits": f"{u:.3f}",
                "trxnMode": "N",
                "trxnCharge": "0.00",
                "trxnTypeFlag": txn_type,
            })

        if random.random() < 0.3 and n_purchases >= 3:
            redeem_units = round(units_remaining * random.uniform(0.05, 0.15), 3)
            redeem_date = base_date + timedelta(days=random.randint(600, 900))
            if redeem_date > datetime(2025, 12, 31):
                redeem_date = datetime(2025, random.randint(7, 12), random.randint(1, 28))
            redeem_nav = fund["nav"] * random.uniform(0.85, 0.95)
            redeem_amt = round(redeem_units * redeem_nav, 2)
            txns.append({
                "email": customer_email,
                "amc": fund["amc"],
                "amcName": fund["fundHouse"],
                "folio": folio,
                "checkDigit": str(random.randint(10, 99)),
                "postedDate": _fmt_date(redeem_date),
                "scheme": h["isin"][-5:],
                "schemeName": fund["schemeName"],
                "purchasePrice": _fmt_nav(redeem_nav),
                "sttTax": "0.00",
                "tax": "0.00",
                "totalTax": "0.00",
                "stampDuty": "",
                "isin": h["isin"],
                "trxnDate": _fmt_date(redeem_date),
                "trxnDesc": "Redemption",
                "trxnAmount": f"-{redeem_amt:.2f}",
                "trxnUnits": f"-{redeem_units:.3f}",
                "trxnMode": "N",
                "trxnCharge": "0.00",
                "trxnTypeFlag": "R",
            })

    txns.sort(key=lambda t: datetime.strptime(t["trxnDate"], "%d-%b-%Y"))
    return txns


def _generate_summaries(holdings, folio, customer_email):
    """Build one summary row per fund held."""
    summaries = []
    broker = random.choice(BROKER_LIST)
    for h in holdings:
        fund = FUND_BY_ISIN[h["isin"]]
        nav = fund["nav"]
        units = h["units"]
        market_value = round(units * nav, 2)
        dec_nav = "4" if nav > 100 else "3" if nav > 10 else "2"

        summaries.append({
            "email": customer_email,
            "amc": fund["amc"],
            "amcName": fund["fundHouse"],
            "folio": folio,
            "scheme": h["isin"][-5:],
            "schemeName": fund["schemeName"],
            "kycStatus": "3",
            "brokerCode": broker[0],
            "brokerName": broker[1],
            "rtaCode": fund["rta"],
            "decimalUnits": "3",
            "decimalAmount": "2",
            "decimalNav": dec_nav,
            "lastTrxnDate": _fmt_date(
                datetime(2025, random.randint(1, 4), random.randint(1, 28))
            ),
            "openingBal": "0.000",
            "marketValue": f"{market_value:.2f}",
            "nav": _fmt_nav(nav, int(dec_nav)),
            "closingBalance": f"{units:.3f}",
            "lastNavDate": "02-APR-2026",
            "isDemat": "N",
            "assetType": fund["assetType"],
            "isin": h["isin"],
            "nomineeStatus": random.choice(["Y", "NA"]),
            "taxStatus": random.choice(["01", "04", "I"]),
            "costValue": f"{h['cost']:.2f}",
        })
    return summaries


def generate_customer_json(cust: dict, idx: int) -> dict:
    folio = f"{cust['folio_prefix']}{10000 + idx * 1111}"

    txns = _generate_transactions(
        cust["holdings"], folio, cust["email"],
        start_year=random.choice([2019, 2020, 2021]),
    )
    summaries = _generate_summaries(cust["holdings"], folio, cust["email"])

    return {
        "pan": cust["pan"],
        "pekrn": "",
        "email": cust["email"],
        "fromDate": "01-Apr-2018",
        "toDate": "05-Apr-2026",
        "data": [
            {
                "dtTransaction": txns,
                "dtSummary": summaries,
            }
        ],
        "investorDetails": {
            "address": cust["address"],
            "email": cust["email"],
            "mobile": cust["mobile"],
            **cust["investor"],
        },
        "reqId": f"{random.randint(1000000,9999999)}-{random.randint(100000000,999999999)}",
    }


def main():
    for idx, cust in enumerate(CUSTOMERS, start=1):
        payload = generate_customer_json(cust, idx)
        fname = f"customer_{idx}_{cust['investor']['investorFirstName'].lower()}.json"
        out_path = OUTPUT_DIR / fname
        out_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        n_txn = len(payload["data"][0]["dtTransaction"])
        n_sum = len(payload["data"][0]["dtSummary"])
        print(f"[{idx}] {out_path.name:45s}  txn={n_txn:3d}  summary={n_sum:2d}")

    print(f"\nGenerated {len(CUSTOMERS)} customer files in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
