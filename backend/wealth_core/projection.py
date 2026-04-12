# project/backend/wealth_core/projection.py

from __future__ import annotations

from typing import List, Dict
import datetime

from .models import ClientSnapshot


def _determine_projection_horizon_year(snapshot: ClientSnapshot) -> int:
    """
    Horizon = last year in which any goal is scheduled.
    If no goals, default to current year + 10.
    """
    base_year = snapshot.current_fy or datetime.date.today().year
    if not snapshot.goals:
        return base_year + 10

    max_goal_year = max((g.target_year for g in snapshot.goals if g.target_year), default=base_year + 10)
    return max(max_goal_year, base_year)


def build_client_projection(snapshot: ClientSnapshot) -> List[Dict]:
    """
    Mortgage-aware, goal-aware projection for a client.

    - Horizon: from snapshot.current_fy (or current year) to last goal year.
    - Income & expenses grow at income_growth_rate / expense_growth_rate.
    - Mortgage EMI amortised monthly with mortgage_interest_rate.
    - One-off inflows/outflows from snapshot.one_off_future_inflows / one_off_future_expenses.
    - Goal outflows from snapshot.goals (amount + inflation_rate).
    - Returns yearly projection as a list of dicts.
    """

    # Base year and horizon
    current_year = snapshot.current_fy or datetime.date.today().year
    last_goal_year = _determine_projection_horizon_year(snapshot)
    years_horizon = last_goal_year - current_year

    # Core parameters with defaults
    annual_income = snapshot.annual_income or 0.0
    income_growth = snapshot.income_growth_rate or 0.0

    annual_expenses = snapshot.annual_expenses or 0.0
    expense_growth = snapshot.expense_growth_rate or 0.0

    tax_rate = snapshot.tax_rate or 0.0
    roi_rate = snapshot.roi_rate or 0.0

    # Mortgage parameters
    mortgage_balance = snapshot.mortgage_balance or 0.0
    mortgage_emi = snapshot.mortgage_emi or 0.0
    mortgage_annual_rate = snapshot.mortgage_interest_rate or 0.0
    mortgage_monthly_rate = mortgage_annual_rate / 12.0

    # Starting net worth based on current balance sheet
    opening_assets = (
        (snapshot.total_mutual_funds or 0.0)
        + (snapshot.total_equities or 0.0)
        + (snapshot.total_debt or 0.0)
        + (snapshot.total_cash_bank or 0.0)
        + (snapshot.properties_value or 0.0)
    )
    opening_liabilities = (snapshot.total_liabilities or 0.0) + mortgage_balance
    net_worth = opening_assets - opening_liabilities

    # One-off inflows / outflows: (year, amount, desc)
    inflows = [(y, amt) for (y, amt, _) in (snapshot.one_off_future_inflows or [])]
    outflows = [(y, amt) for (y, amt, _) in (snapshot.one_off_future_expenses or [])]

    rows: List[Dict] = []

    for i in range(years_horizon + 1):
        fy = current_year + i

        # 1. Income & expenses with growth
        income_pre_tax = annual_income * ((1 + income_growth) ** i)
        income_post_tax = income_pre_tax * (1 - tax_rate)

        regular_expenses = annual_expenses * ((1 + expense_growth) ** i)

        # 2. Mortgage amortisation (monthly loop)
        annual_emi_paid = 0.0
        for _ in range(12):
            if mortgage_balance <= 0:
                break
            interest = mortgage_balance * mortgage_monthly_rate
            emi = min(mortgage_emi, mortgage_balance + interest) if mortgage_emi > 0 else 0.0
            principal = emi - interest
            mortgage_balance -= principal
            annual_emi_paid += emi

        # 3. One-off flows
        one_off_inflows_year = sum(amt for y, amt in inflows if y == fy)
        one_off_outflows_year = sum(amt for y, amt in outflows if y == fy)

        # 4. Goal outflows
        goal_outflow = 0.0
        for g in snapshot.goals:
            if g.target_year == fy and g.amount:
                # If client didn't provide inflation, default to expense_growth
                inf = g.inflation_rate if g.inflation_rate is not None else expense_growth
                years_from_start = max(fy - current_year, 0)
                goal_outflow += g.amount * ((1 + inf) ** years_from_start)

        # 5. Wealth movement
        opening_net_worth = net_worth
        roi_earned = opening_net_worth * roi_rate

        net_cash_flow_before_goals = (
            income_post_tax
            - regular_expenses
            - annual_emi_paid
            + one_off_inflows_year
            - one_off_outflows_year
        )

        net_worth = opening_net_worth + roi_earned + net_cash_flow_before_goals - goal_outflow

        rows.append(
            {
                "year": fy,
                "income_post_tax": round(income_post_tax, 2),
                "regular_expenses": round(regular_expenses, 2),
                "mortgage_emi_paid": round(annual_emi_paid, 2),
                "one_off_inflows": round(one_off_inflows_year, 2),
                "one_off_outflows": round(one_off_outflows_year, 2),
                "goal_outflow": round(goal_outflow, 2),
                "opening_net_worth": round(opening_net_worth, 2),
                "roi_earned": round(roi_earned, 2),
                "closing_net_worth": round(net_worth, 2),
                "mortgage_balance": round(mortgage_balance, 2),
            }
        )

    return rows
