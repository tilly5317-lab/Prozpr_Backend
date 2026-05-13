from __future__ import annotations

from ..models import AllocationInput, FutureInvestment, Step1Output
from ..tables import EMERGENCY_FUND_MONTHS
from ..utils import round_to_100


FUTURE_INVESTMENT_MSG = (
    "Your current corpus is just shy of the safety cushion you need right now. "
    "Think of the remaining amount as wealth to build through your upcoming "
    "monthly investments — with a modest step-up in savings you'll have this "
    "reserve fully in place and remain firmly on track for every other goal."
)


def run(inp: AllocationInput) -> Step1Output:
    if not inp.emergency_fund_needed:
        emergency_fund_months = 0
        emergency_fund_amount = 0
    else:
        key = "primary_income_from_portfolio" if inp.primary_income_from_portfolio else "standard"
        emergency_fund_months = EMERGENCY_FUND_MONTHS[key]
        emergency_fund_amount = round_to_100(emergency_fund_months * inp.monthly_household_expense)

    nfa = inp.net_financial_assets
    nfa_carveout_amount = round_to_100(abs(nfa)) if (nfa is not None and nfa < 0) else 0

    total_emergency = emergency_fund_amount + nfa_carveout_amount
    total_corpus_int = int(inp.total_corpus)

    if total_emergency > total_corpus_int:
        future_investment_amount = total_emergency - total_corpus_int
        remaining_corpus = 0
        future_investment = FutureInvestment(
            bucket="emergency",
            future_investment_amount=future_investment_amount,
            message=FUTURE_INVESTMENT_MSG,
        )
    else:
        remaining_corpus = total_corpus_int - total_emergency
        future_investment = None

    subgroup_amounts: dict[str, int] = {"debt_subgroup": total_emergency}

    return Step1Output(
        emergency_fund_months=emergency_fund_months,
        emergency_fund_amount=emergency_fund_amount,
        nfa_carveout_amount=nfa_carveout_amount,
        total_emergency=total_emergency,
        remaining_corpus=remaining_corpus,
        future_investment=future_investment,
        subgroup_amounts=subgroup_amounts,
    )
