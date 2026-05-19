"""Tier 1: derive from CSV + scheme name + rules.

Outputs per scheme:
  isin_growth, isin_div_reinvest, asset_class, asset_subcategory,
  plan_class (direct/regular), div_or_growth (paid/growth),
  investor (retail/insti), parent_fund_house,
  st_rate, st_period, lt_rate, lt_period
"""
import re


def _clean_isin(v) -> str:
    """Normalize an ISIN cell — handles None, NaN (str 'nan'), and whitespace."""
    if v is None:
        return ""
    s = str(v).strip()
    if s.lower() in {"nan", "none"}:
        return ""
    return s

# AMFI broad mapping. Equity sub-cats per SEBI categorization.
EQUITY_SUBCATS = {
    "Large Cap Fund", "Mid Cap Fund", "Small Cap Fund", "Large & Mid Cap Fund",
    "Multi Cap Fund", "Flexi Cap Fund", "Focused Fund", "ELSS",
    "Sectoral/ Thematic", "Value Fund", "Contra Fund", "Dividend Yield Fund",
}
# Funds taxed as equity: equity funds + arbitrage + hybrid funds with >=65% equity
EQUITY_TAXED_HYBRIDS = {"Aggressive Hybrid Fund", "Arbitrage Fund", "Equity Savings"}
# Hybrid funds whose tax depends on portfolio mix; default to debt taxation
HYBRID_DEBT_TAXED = {
    "Conservative Hybrid Fund", "Multi Asset Allocation",
    "Balanced Hybrid Fund", "Dynamic Asset Allocation or Balanced Advantage",
}
DEBT_SUBCATS = {
    "Liquid Fund", "Overnight Fund", "Low Duration Fund",
    "Ultra Short Duration Fund", "Money Market Fund", "Money Market",
    "Short Duration Fund", "Medium Duration Fund",
    "Medium to Long Duration Fund", "Long Duration Fund",
    "Dynamic Bond", "Corporate Bond Fund", "Credit Risk Fund",
    "Banking and PSU Fund", "Gilt Fund",
    "Gilt Fund with 10 year constant duration", "Floater Fund",
    "Income", "IDF",
}
OTHER_SUBCATS = {
    "Index Funds", "Other  ETFs", "Gold ETF", "FoF Domestic", "FoF Overseas",
    "Retirement Fund", "Children’s Fund", "Children's Fund",
}


def asset_class_for(sub_category: str, scheme_name: str = "") -> str:
    s = sub_category.strip() if isinstance(sub_category, str) else ""
    if s in EQUITY_SUBCATS:
        return "Equity"
    if s in EQUITY_TAXED_HYBRIDS or s in HYBRID_DEBT_TAXED:
        return "Hybrid"
    if s in DEBT_SUBCATS:
        return "Debt"
    if s == "Gold ETF":
        return "Commodity"
    if s in {"Index Funds", "Other  ETFs"}:
        # Can't tell from sub_cat alone; infer from name
        nm = (scheme_name or "").lower()
        if any(k in nm for k in ("nifty", "sensex", "midcap", "smallcap", "next 50", "bank", "pharma", "it ", "fmcg", "auto")):
            return "Equity"
        if any(k in nm for k in ("liquid", "gilt", "psu", "bond", "g-sec", "duration", "money market", "tbill")):
            return "Debt"
        if "gold" in nm:
            return "Commodity"
        return "Equity"  # default for index/ETF
    if s in {"FoF Domestic", "FoF Overseas"}:
        return "FoF"
    if s in {"Retirement Fund", "Children’s Fund", "Children's Fund"}:
        return "Solution-oriented"
    return "Other"


def plan_class(scheme_name: str) -> str:
    s = (scheme_name or "").lower()
    if "direct" in s:
        return "Direct"
    if "regular" in s:
        return "Regular"
    return "Regular"  # AMFI legacy default


DIV_KEYWORDS = ("idcw", "dividend", "income distribution", "div ")


def div_or_growth(scheme_name: str) -> str:
    s = (scheme_name or "").lower()
    if any(k in s for k in DIV_KEYWORDS):
        if "reinvest" in s:
            return "Dividend Reinvestment"
        if "payout" in s:
            return "Dividend Payout"
        return "Dividend (IDCW)"
    if "bonus" in s:
        return "Bonus"
    return "Growth"


def investor_type(scheme_name: str) -> str:
    s = (scheme_name or "").lower()
    if any(k in s for k in (" insti", "institutional", "instl")):
        return "Institutional"
    return "Retail"


def tax_treatment(asset_class: str, sub_category: str) -> dict:
    """Indian MF taxation post-Apr 2023 regime (assuming holdings acquired now).

    Equity-taxed: STCG (≤12m) 20%, LTCG (>12m) 12.5% over 1.25L.
    Debt (acquired after 1-Apr-2023): slab rate at any holding period (no LTCG).
    Hybrid (debt-oriented): same as debt.
    Gold/Intl FoF/Commodity (post-Apr 2023): slab rate.
    """
    sub = sub_category or ""
    if asset_class == "Equity" or sub in EQUITY_TAXED_HYBRIDS:
        return {
            "st_rate": "20%", "st_period": "≤ 12 months",
            "lt_rate": "12.5% (above ₹1.25L exemption)", "lt_period": "> 12 months",
        }
    if asset_class == "Debt" or sub in HYBRID_DEBT_TAXED or asset_class in {"FoF", "Commodity"}:
        return {
            "st_rate": "Slab rate", "st_period": "Any (post 1-Apr-2023)",
            "lt_rate": "Slab rate (no indexation)", "lt_period": "Any (post 1-Apr-2023)",
        }
    return {"st_rate": "Slab rate", "st_period": "—", "lt_rate": "Slab rate", "lt_period": "—"}


def derive_row(row: dict) -> dict:
    name = row.get("schemeName", "")
    sub = row.get("sub_category", "")
    ac = asset_class_for(sub, name)
    tax = tax_treatment(ac, sub)
    return {
        "isin_growth": _clean_isin(row.get("isinGrowth")),
        "isin_div_reinvest": _clean_isin(row.get("isinDivReinvestment")),
        "asset_class": ac,
        "asset_subcategory": sub,
        "plan_class": plan_class(name),
        "div_or_growth": div_or_growth(name),
        "investor": investor_type(name),
        "parent_fund_house": row.get("fundHouse", ""),
        **tax,
    }


if __name__ == "__main__":
    import pandas as pd
    df = pd.read_csv("latest_nav_active.csv")
    sample = df.iloc[[0, 100, 1000, 5000, 8000]]
    for _, r in sample.iterrows():
        print(r["schemeName"][:60])
        print(" ", derive_row(r.to_dict()))
