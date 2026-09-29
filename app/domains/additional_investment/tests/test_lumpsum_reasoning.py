"""Per-fund "why this fund" lines rebuilt from the lumpsum deficit facts."""

from __future__ import annotations

from app.domains.additional_investment.services.lumpsum_reasoning import build_fund_reason


def _reason(row, *, fund="Bluechip Fund", sub_category="Large Cap Fund",
            subgroup="low_beta_equities", amount=200_000.0):
    return build_fund_reason(
        recommended_fund=fund,
        sub_category=sub_category,
        asset_subgroup=subgroup,
        rank=1,
        amount_inr=amount,
        deficit_row=row,
    )


def _arbitrage_reason(row):
    return _reason(row, fund="Arb Fund", sub_category="Arbitrage Fund",
                   subgroup="arbitrage", amount=100_000.0)


def test_a_gap_row_says_what_counts_toward_the_target():
    row = {"ideal_inr": 600_000.0, "current_inr": 400_000.0, "gap_inr": 200_000.0,
           "goal_row": False}
    assert _reason(row) == (
        "₹4 lakh of what you already hold counts toward your large-cap equity target of "
        "₹6 lakh — a ₹2 lakh shortfall. We put ₹2 lakh into Bluechip Fund, our "
        "top-ranked Large Cap Fund pick, to help close that gap."
    )


def test_a_run_persisted_before_goal_row_reads_as_a_plain_gap_row():
    row = {"ideal_inr": 600_000.0, "current_inr": 400_000.0, "gap_inr": 200_000.0}
    assert _reason(row).startswith("₹4 lakh of what you already hold counts toward")


def test_the_goal_row_never_calls_goal_money_a_holding_in_that_fund_type():
    row = {"ideal_inr": 250_000.0, "current_inr": 150_000.0, "gap_inr": 100_000.0,
           "goal_row": True}
    assert _arbitrage_reason(row) == (
        "Your near-term goals and plan need ₹2.5 lakh in arbitrage; ₹1.5 lakh of the "
        "debt and arbitrage funds you already hold counts toward that — a ₹1 lakh "
        "shortfall. We put ₹1 lakh into Arb Fund, our top-ranked Arbitrage Fund pick, "
        "to help close that gap."
    )


def test_a_nearly_covered_goal_row_tops_up():
    row = {"ideal_inr": 250_000.0, "current_inr": 249_950.0, "gap_inr": 50.0,
           "goal_row": True}
    assert _arbitrage_reason(row) == (
        "Your near-term goals and plan are already close to covered in arbitrage, so "
        "₹1 lakh tops it up through Arb Fund, our top-ranked Arbitrage Fund pick."
    )


def test_a_nearly_covered_plain_row_keeps_the_top_up_line():
    row = {"ideal_inr": 600_000.0, "current_inr": 599_950.0, "gap_inr": 50.0,
           "goal_row": False}
    assert _reason(row) == (
        "Your large-cap equity is already close to its goal-based ideal, so ₹2 lakh "
        "tops it up through Bluechip Fund, our top-ranked Large Cap Fund pick."
    )


def test_a_plain_row_with_nothing_held_yet():
    row = {"ideal_inr": 600_000.0, "current_inr": 0.0, "gap_inr": 600_000.0,
           "goal_row": False}
    assert _reason(row) == (
        "You don't hold anything toward your large-cap equity target of ₹6 lakh yet — "
        "we put ₹2 lakh into Bluechip Fund, our top-ranked Large Cap Fund pick, to "
        "help close that gap."
    )


def test_a_goal_row_with_nothing_held_yet():
    row = {"ideal_inr": 250_000.0, "current_inr": 0.0, "gap_inr": 250_000.0,
           "goal_row": True}
    assert _arbitrage_reason(row) == (
        "Your near-term goals and plan need ₹2.5 lakh in arbitrage, and nothing you "
        "hold counts toward it yet — we put ₹1 lakh into Arb Fund, our top-ranked "
        "Arbitrage Fund pick."
    )


def test_no_deficit_row_keeps_the_rank_line():
    assert _reason(None) == (
        "₹2 lakh goes into Bluechip Fund, our top-ranked Large Cap Fund pick for "
        "large-cap equity, in line with your goal-based target mix."
    )
