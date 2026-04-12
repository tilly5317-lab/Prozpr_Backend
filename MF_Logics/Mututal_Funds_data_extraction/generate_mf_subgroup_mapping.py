import csv
import re


# ---------------------------------------------------------------------------
# Master mapping: asset_class_subcategory → (asset_class_sebi, asset_class, asset_subgroup)
# ---------------------------------------------------------------------------
SUBCAT_TO_MAPPING = {
    # Equity
    'Multi Cap Fund':                    ('equity_schemes', 'equities', 'medium_beta_equities'),
    'Large Cap Fund':                    ('equity_schemes', 'equities', 'low_beta_equities'),
    'Large & Mid Cap Fund':              ('equity_schemes', 'equities', 'medium_beta_equities'),
    'Mid Cap Fund':                      ('equity_schemes', 'equities', 'medium_beta_equities'),
    'Small Cap Fund':                    ('equity_schemes', 'equities', 'high_beta_equities'),
    'Flexi Cap Fund':                    ('equity_schemes', 'equities', 'medium_beta_equities'),
    'Dividend Yield Fund':               ('equity_schemes', 'equities', 'dividend_equities'),
    'Value Fund':                        ('equity_schemes', 'equities', 'value_equities'),
    'Contra Fund':                       ('equity_schemes', 'equities', 'value_equities'),
    'Focused Fund':                      ('equity_schemes', 'equities', 'high_beta_equities'),
    'Sectoral Fund':                     ('equity_schemes', 'equities', 'sector_equities'),
    'Thematic Fund':                     ('equity_schemes', 'equities', 'sector_equities'),
    'ELSS Tax Saver Fund':               ('equity_schemes', 'equities', 'tax_efficient_equities'),

    # Debt
    'Overnight Fund':                    ('debt_schemes', 'debt', 'near_debt'),
    'Liquid Fund':                       ('debt_schemes', 'debt', 'near_debt'),
    'Ultra Short Fund (3-6 months)':     ('debt_schemes', 'debt', 'near_debt'),
    'Ultra Short to Short Term Fund (6-12 months)': ('debt_schemes', 'debt', 'short_debt'),
    'Money Market Fund':                 ('debt_schemes', 'debt', 'short_debt'),
    'Short Term Fund (1-3 years)':       ('debt_schemes', 'debt', 'short_debt'),
    'Medium Term Fund (3-4 years)':      ('debt_schemes', 'debt', 'medium_debt'),
    'Medium Term to Long Term Fund (4-7 years)': ('debt_schemes', 'debt', 'medium_debt'),
    'Long Term Fund (above 7 years)':    ('debt_schemes', 'debt', 'long_duration_debt'),
    'Dynamic Term Fund':                 ('debt_schemes', 'debt', 'medium_debt'),
    'Corporate Bond Fund':               ('debt_schemes', 'debt', 'high_risk_debt'),
    'Credit Risk Fund':                  ('debt_schemes', 'debt', 'high_risk_debt'),
    'Banking and PSU Debt Fund':         ('debt_schemes', 'debt', 'high_risk_debt'),
    'Gilt Fund':                         ('debt_schemes', 'debt', 'medium_debt'),
    '10-Year Constant Maturity Gilt Fund': ('debt_schemes', 'debt', 'long_duration_debt'),
    'Floating Interest Rates Fund':      ('debt_schemes', 'debt', 'floating_debt'),
    'Sectoral Debt Fund':                ('debt_schemes', 'debt', 'high_risk_debt'),

    # Hybrid
    'Conservative Hybrid Fund (Equity 10-25%, Debt 75-90%)': ('hybrid_schemes', 'debt', 'Others'),
    'Balanced Hybrid Fund (Equity 40-60%, Debt 40-60%)':     ('hybrid_schemes', 'debt', 'Others'),
    'Aggressive Hybrid Fund (Equity 65-80%, Debt 20-35%)':   ('hybrid_schemes', 'equities', 'medium_beta_equities'),
    'Dynamic Asset Allocation Fund':     ('hybrid_schemes', 'equities', 'medium_beta_equities'),
    'Multi-Asset Allocation Fund':       ('hybrid_schemes', 'equities', 'medium_beta_equities'),
    'Arbitrage Fund':                    ('hybrid_schemes', 'others', 'others'),

    # Index / ETF
    'Large Cap Index Linked (Index/ETF)':             ('other_schemes', 'equities', 'low_beta_equities'),
    'Multi Cap Index Linked (Index/ETF)':              ('other_schemes', 'equities', 'medium_beta_equities'),
    'Sectoral & Thematic Index Linked (Index/ETF)':    ('other_schemes', 'equities', 'sector_equities'),
    'Gold Linked (Index/ETF)':                         ('other_schemes', 'others', 'gold_commodities'),
    'Silver Linked (Index/ETF)':                       ('other_schemes', 'others', 'silver_commodities'),
    'Others (Index/ETF)':                              ('other_schemes', '', 'others'),

    # FoF
    'US Linked (FoF)':                   ('other_schemes', 'equities', 'us_equities'),
    'China Linked (FoF)':                ('other_schemes', 'equities', 'china_equities'),
    'Others (FoF)':                      ('other_schemes', 'others', 'others_fofs'),
}


# ---------------------------------------------------------------------------
# Direct mapping: CSV sub_category → asset_class_subcategory
# ---------------------------------------------------------------------------
DIRECT_SUBCAT_MAP = {
    'Multi Cap Fund':       'Multi Cap Fund',
    'Large Cap Fund':       'Large Cap Fund',
    'Large & Mid Cap Fund': 'Large & Mid Cap Fund',
    'Mid Cap Fund':         'Mid Cap Fund',
    'Small Cap Fund':       'Small Cap Fund',
    'Flexi Cap Fund':       'Flexi Cap Fund',
    'Dividend Yield Fund':  'Dividend Yield Fund',
    'Value Fund':           'Value Fund',
    'Contra Fund':          'Contra Fund',
    'Focused Fund':         'Focused Fund',
    'Sectoral/ Thematic':   'Sectoral Fund',
    'ELSS':                 'ELSS Tax Saver Fund',

    'Overnight Fund':       'Overnight Fund',
    'Liquid Fund':          'Liquid Fund',
    'Ultra Short Duration Fund': 'Ultra Short Fund (3-6 months)',
    'Low Duration Fund':    'Ultra Short to Short Term Fund (6-12 months)',
    'Money Market':         'Money Market Fund',
    'Money Market Fund':    'Money Market Fund',
    'Short Duration Fund':  'Short Term Fund (1-3 years)',
    'Medium Duration Fund': 'Medium Term Fund (3-4 years)',
    'Medium to Long Duration Fund': 'Medium Term to Long Term Fund (4-7 years)',
    'Long Duration Fund':   'Long Term Fund (above 7 years)',
    'Dynamic Bond':         'Dynamic Term Fund',
    'Corporate Bond Fund':  'Corporate Bond Fund',
    'Credit Risk Fund':     'Credit Risk Fund',
    'Banking and PSU Fund': 'Banking and PSU Debt Fund',
    'Gilt Fund':            'Gilt Fund',
    'Gilt Fund with 10 year constant duration': '10-Year Constant Maturity Gilt Fund',
    'Floater Fund':         'Floating Interest Rates Fund',

    'Conservative Hybrid Fund':  'Conservative Hybrid Fund (Equity 10-25%, Debt 75-90%)',
    'Balanced Hybrid Fund':      'Balanced Hybrid Fund (Equity 40-60%, Debt 40-60%)',
    'Aggressive Hybrid Fund':    'Aggressive Hybrid Fund (Equity 65-80%, Debt 20-35%)',
    'Dynamic Asset Allocation or Balanced Advantage': 'Dynamic Asset Allocation Fund',
    'Multi Asset Allocation':    'Multi-Asset Allocation Fund',
    'Arbitrage Fund':            'Arbitrage Fund',

    'Gold ETF':                  'Gold Linked (Index/ETF)',
}

BLANK_SUBCATS = {"Children\u2019s Fund", 'Retirement Fund', 'Equity Savings', 'IDF', 'Income'}


# ---------------------------------------------------------------------------
# Name-based classification patterns
# ---------------------------------------------------------------------------
DEBT_INDEX_PATTERNS = [
    r'\bcrisil\b', r'\bibx\b', r'\bgilt\b', r'\bsdl\b',
    r'\bg-sec\b', r'\bgsec\b', r'treasury\s*bill',
    r'\baaa\b', r'debt\s*(index|passive)',
    r'bond\s*(index|etf)', r'overnight\s*rate', r'\b1d\s*rate\b',
    r'target\s*maturity', r'bharat\s*bond', r'cpse\s*bond',
    r'\bliquid\s*(rate|overnight)\b', r'\bbse\s*liquid\s*rate\b',
    r'\bnifty\s*psu\s*bond\b',
]

LARGE_CAP_PATTERNS = [
    r'\bnifty\s*50\b(?!\d)',
    r'\bsensex\b(?!\s*next)',
    r'\bnifty\s*100\b',
    r'\bbse\s*100\b',
    r'\bbse\s*30\b',
    r'\bnifty\s+index\b',       # bare "Nifty Index Fund" = Nifty 50
    r'\bnifty\s+etf\b',         # bare "Nifty ETF" = Nifty 50
    r'\bnifty\s+exchange\b',    # "Nifty Exchange Traded Fund" = Nifty 50
    r'\btop\s*10\s*equal\b',    # Nifty Top 10 Equal Weight
    r'\btop\s*20\s*equal\b',    # Nifty Top 20 Equal Weight
    r'\btop\s*15\s*equal\b',    # Nifty Top 15 Equal Weight
]

SECTORAL_PATTERNS = [
    r'\bpharma\b', r'\bhealthcare\b', r'\bhealth\s*care\b',
    r'(?:nifty|bse)\s+it\b', r'\btechnology\b',
    r'\binfra\b', r'\binfrastructure\b',
    r'\bbank\b', r'\bbanking\b',
    r'\bfinancial\s*services?\b', r'\bfinserv\b',
    r'\bdefence\b', r'\bdefense\b',
    r'\bauto\b', r'\bautomobile\b', r'\bautomotive\b',
    r'\bfmcg\b', r'\bconsumption\b', r'\bconsumer\b',
    r'\benergy\b', r'\boil\b',
    r'\bpse\b', r'\bpsu\b', r'\bcpse\b',
    r'\bmanufacturing\b',
    r'\breal\s*estate\b', r'\breit\b', r'\brealty\b',
    r'\bev\b', r'\belectric\s*vehicle\b',
    r'\bdigital\b', r'\bmnc\b',
    r'\bprivate\s*bank\b',
    r'\bmedia\b', r'\bmetal\b', r'\btelecom\b',
    r'\bshariah\b', r'\bmobility\b',
    r'\bnon.?cyclical\b', r'\bcyclical\b',
    r'\bhousehold\b', r'\btourism\b', r'\bhousing\b',
    r'\bnatural\s*resources?\b', r'\bcommodit',
    r'\bcapital\s*markets?\b',
    r'\bprivate\s*sector\b',
    r'\btransportation\b', r'\blogistics\b',
    r'\bsemiconductor\b',
    r'\binternet\b', r'\bservices\s+sector\b',
    r'\bchemical\b', r'\bchemicals\b',
    r'\bpower\b', r'\brailway\b',
    r'\bhospital\b', r'\bipu\b',
    r'\bgrowth\s*sectors?\s*15\b',
    r'\bdividend\s*opportunities\b',
    r'\bselect\s*ipo\b',
    r'\bnew\s*age\b', r'\bnifty\s+it\b',
    r'\besg\b',
    r'\bbharat\s*22\b',
]

INTERNATIONAL_PATTERNS = [
    r'\bnasdaq\b', r'\bnyse\b', r'\bhang\s*seng\b',
    r'\bs&p\s*500\b',
]

US_FOF_PATTERNS = [r'\bus\b', r'\bnasdaq\b', r'\bs&p\s*500\b', r'\bamerica\b']
CHINA_FOF_PATTERNS = [r'\bchina\b', r'\bhang\s*seng\b']


def _match_any(patterns, text):
    return any(re.search(p, text) for p in patterns)


def classify_index_or_etf(name):
    """Classify Index Funds / ETFs by name into user-defined subcategories."""
    name_lower = name.lower()

    if _match_any(DEBT_INDEX_PATTERNS, name_lower):
        return ''

    if re.search(r'\bgold\b', name_lower):
        return 'Gold Linked (Index/ETF)'
    if re.search(r'\bsilver\b', name_lower):
        return 'Silver Linked (Index/ETF)'

    if _match_any(LARGE_CAP_PATTERNS, name_lower):
        return 'Large Cap Index Linked (Index/ETF)'

    if _match_any(SECTORAL_PATTERNS, name_lower):
        return 'Sectoral & Thematic Index Linked (Index/ETF)'

    if _match_any(INTERNATIONAL_PATTERNS, name_lower):
        return 'Others (Index/ETF)'

    return 'Multi Cap Index Linked (Index/ETF)'


def classify_fof_overseas(name):
    name_lower = name.lower()
    if _match_any(US_FOF_PATTERNS, name_lower):
        return 'US Linked (FoF)'
    if _match_any(CHINA_FOF_PATTERNS, name_lower):
        return 'China Linked (FoF)'
    return 'Others (FoF)'


def classify_fof_domestic(name):
    name_lower = name.lower()
    if re.search(r'\bgold\b', name_lower):
        return 'Gold Linked (Index/ETF)'
    if re.search(r'\bsilver\b', name_lower):
        return 'Silver Linked (Index/ETF)'
    return 'Others (FoF)'


def get_subcategory(sub_category, scheme_name):
    """Determine asset_class_subcategory from CSV sub_category and fund name."""
    if sub_category in DIRECT_SUBCAT_MAP:
        return DIRECT_SUBCAT_MAP[sub_category]
    if sub_category in BLANK_SUBCATS:
        return ''
    if sub_category in ('Index Funds', 'Other  ETFs'):
        return classify_index_or_etf(scheme_name)
    if sub_category == 'FoF Overseas':
        return classify_fof_overseas(scheme_name)
    if sub_category == 'FoF Domestic':
        return classify_fof_domestic(scheme_name)
    return ''


def main():
    input_file = 'latest_nav_active.csv'
    output_file = 'mf_subgroup_mapped.csv'

    rows_written = 0
    blank_subcats = set()

    with open(input_file, 'r', newline='') as fin, \
         open(output_file, 'w', newline='') as fout:

        reader = csv.DictReader(fin)
        fieldnames = reader.fieldnames + [
            'asset_class_subcategory', 'asset_class_sebi', 'asset_class', 'asset_subgroup'
        ]
        writer = csv.DictWriter(fout, fieldnames=fieldnames)
        writer.writeheader()

        for row in reader:
            sub_cat = row['sub_category']
            scheme_name = row['schemeName']

            subcat = get_subcategory(sub_cat, scheme_name)
            row['asset_class_subcategory'] = subcat

            if subcat and subcat in SUBCAT_TO_MAPPING:
                sebi, ac, asg = SUBCAT_TO_MAPPING[subcat]
            else:
                sebi, ac, asg = '', '', ''
                if sub_cat not in BLANK_SUBCATS:
                    blank_subcats.add(sub_cat)

            row['asset_class_sebi'] = sebi
            row['asset_class'] = ac
            row['asset_subgroup'] = asg

            writer.writerow(row)
            rows_written += 1

    print(f"Wrote {rows_written} rows to {output_file}")
    if blank_subcats:
        print(f"Sub-categories left blank: {sorted(blank_subcats)}")


if __name__ == '__main__':
    main()
