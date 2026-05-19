"""Map sub_category -> benchmark scheme_code (index-fund proxy for TRI).

Free fixed-rate proxies for debt (no clean free TRI series).
"""
from __future__ import annotations
# Index fund proxies (Direct Growth) for equity TRI benchmarks.
NIFTY_50 = 120716           # UTI Nifty 50 Index Fund - Direct Growth
NIFTY_NEXT_50 = 143341      # UTI Nifty Next 50 Index Fund - Direct Growth
NIFTY_MIDCAP_150 = 148726   # Nippon India Nifty Midcap 150 Index - Direct Growth
NIFTY_SMALLCAP_250 = 148519 # Nippon India Nifty Smallcap 250 Index - Direct Growth
NIFTY_500 = 152731          # Axis Nifty 500 Index Fund - Direct Growth
NIFTY_BANK = 149804         # Navi Nifty Bank Index Fund - Direct Growth

# Hybrid blend marker (computed in tier2)
HYBRID_AGGRESSIVE = "BLEND_65_35"   # 65% Nifty 50 + 35% fixed 6.5%
HYBRID_CONSERVATIVE = "BLEND_25_75"

# Fixed-rate proxy: annualized return %
RISK_FREE = 6.5

SUBCAT_BENCHMARK = {
    # Equity
    "Large Cap Fund": NIFTY_50,
    "Large & Mid Cap Fund": NIFTY_500,
    "Mid Cap Fund": NIFTY_MIDCAP_150,
    "Small Cap Fund": NIFTY_SMALLCAP_250,
    "Multi Cap Fund": NIFTY_50,        # proxy: no long-history Nifty 500 fund
    "Flexi Cap Fund": NIFTY_50,
    "Focused Fund": NIFTY_50,
    "ELSS": NIFTY_50,
    "Value Fund": NIFTY_50,
    "Contra Fund": NIFTY_50,
    "Dividend Yield Fund": NIFTY_50,
    "Sectoral/ Thematic": NIFTY_50,  # default; sectoral specifics need name parsing
    # Hybrid
    "Aggressive Hybrid Fund": HYBRID_AGGRESSIVE,
    "Conservative Hybrid Fund": HYBRID_CONSERVATIVE,
    "Balanced Hybrid Fund": HYBRID_AGGRESSIVE,
    "Dynamic Asset Allocation or Balanced Advantage": HYBRID_AGGRESSIVE,
    "Multi Asset Allocation": HYBRID_AGGRESSIVE,
    "Equity Savings": HYBRID_CONSERVATIVE,
    "Arbitrage Fund": "FIXED_6.5",
    # Debt — fixed-rate proxy for now
    "Liquid Fund": "FIXED_6.5",
    "Overnight Fund": "FIXED_6.5",
    "Low Duration Fund": "FIXED_6.5",
    "Ultra Short Duration Fund": "FIXED_6.5",
    "Money Market Fund": "FIXED_6.5",
    "Money Market": "FIXED_6.5",
    "Short Duration Fund": "FIXED_6.5",
    "Medium Duration Fund": "FIXED_6.5",
    "Medium to Long Duration Fund": "FIXED_6.5",
    "Long Duration Fund": "FIXED_6.5",
    "Dynamic Bond": "FIXED_6.5",
    "Corporate Bond Fund": "FIXED_6.5",
    "Credit Risk Fund": "FIXED_6.5",
    "Banking and PSU Fund": "FIXED_6.5",
    "Gilt Fund": "FIXED_6.5",
    "Gilt Fund with 10 year constant duration": "FIXED_6.5",
    "Floater Fund": "FIXED_6.5",
    "Income": "FIXED_6.5",
    "IDF": "FIXED_6.5",
    # Other
    "Index Funds": NIFTY_50,         # default, refine via name
    "Other  ETFs": NIFTY_50,
    "Gold ETF": "FIXED_8.0",         # rough gold long-term avg
    "FoF Domestic": NIFTY_500,
    "FoF Overseas": "FIXED_10.0",    # rough global equity proxy
    "Retirement Fund": NIFTY_500,
    "Children’s Fund": NIFTY_500,
    "Children's Fund": NIFTY_500,
}


def benchmark_for(sub_category: str, scheme_name: str = "") -> str | int:
    """Return either a scheme_code (int) or a string token (FIXED_X.X / BLEND_*)."""
    nm = (scheme_name or "").lower()
    sub = sub_category or ""
    # Index fund / ETF: try to read the underlying index from the name
    if sub in {"Index Funds", "Other  ETFs"}:
        if "midcap 150" in nm or "midcap150" in nm:
            return NIFTY_MIDCAP_150
        if "smallcap 250" in nm or "smallcap250" in nm:
            return NIFTY_SMALLCAP_250
        if "nifty 500" in nm or "nifty500" in nm:
            return NIFTY_500
        if "next 50" in nm or "next50" in nm:
            return NIFTY_NEXT_50
        if "bank" in nm:
            return NIFTY_BANK
        if "gold" in nm:
            return "FIXED_8.0"
        if any(k in nm for k in ("liquid", "gilt", "psu", "bond", "g-sec", "duration", "money market", "tbill")):
            return "FIXED_6.5"
        if "nifty" in nm or "sensex" in nm:
            return NIFTY_50
        return NIFTY_50
    return SUBCAT_BENCHMARK.get(sub, NIFTY_500)


def hybrid_blend(token: str) -> tuple[float, float]:
    """Return (equity_weight, fixed_rate_pct) for blend tokens."""
    if token == "BLEND_65_35":
        return 0.65, RISK_FREE
    if token == "BLEND_25_75":
        return 0.25, RISK_FREE
    return 1.0, 0.0


def fixed_rate(token: str) -> float:
    return float(token.split("_", 1)[1])


if __name__ == "__main__":
    for s in ["Large Cap Fund", "Mid Cap Fund", "Liquid Fund", "Aggressive Hybrid Fund", "Index Funds", "Sectoral/ Thematic"]:
        print(s, "->", benchmark_for(s))
