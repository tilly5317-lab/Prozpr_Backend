from __future__ import annotations

from ..models import (
    AggregatedRow,
    Step1Output,
    Step2Output,
    Step3Output,
    Step4Output,
    Step5Output,
)
from ..utils import round_to_100


CANONICAL_SUBGROUP_ORDER = [
    "debt_subgroup",
    "short_debt",
    "arbitrage",
    "arbitrage_plus_income",
    "tax_efficient_equities",
    "multi_asset",
    "low_beta_equities",
    "medium_beta_equities",
    "high_beta_equities",
    "value_equities",
    "sector_equities",
    "us_equities",
    "gold_commodities",
]


def run(
    total_corpus: float,
    step1: Step1Output,
    step2: Step2Output,
    step3: Step3Output,
    step4: Step4Output,
) -> Step5Output:
    rows: list[AggregatedRow] = []

    for sg in CANONICAL_SUBGROUP_ORDER:
        emergency = step1.subgroup_amounts.get(sg, 0)
        short_term = step2.subgroup_amounts.get(sg, 0)
        medium_term = step3.subgroup_amounts.get(sg, 0)
        long_term = step4.subgroup_amounts.get(sg, 0)
        total = emergency + short_term + medium_term + long_term
        if total > 0:
            rows.append(AggregatedRow(
                subgroup=sg,
                emergency=emergency,
                short_term=short_term,
                medium_term=medium_term,
                long_term=long_term,
                total=total,
            ))

    grand_total = sum(row.total for row in rows)
    grand_total_matches_corpus = grand_total == round_to_100(total_corpus)

    return Step5Output(
        rows=rows,
        grand_total=grand_total,
        grand_total_matches_corpus=grand_total_matches_corpus,
    )
