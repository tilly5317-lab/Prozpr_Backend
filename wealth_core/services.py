# save/load, cashflow, balance sheet, snapshot builder


# ┌──────────────────────────────────────────────────┐
# │  Database Layer (save/load clients)              │
# ├──────────────────────────────────────────────────┤
# │  Financial Calculations (cash flow, balance)     │
# ├──────────────────────────────────────────────────┤
# │  Data Transformation (state → snapshot)          │
# └──────────────────────────────────────────────────┘

from __future__ import annotations
from .statement_parser import parse_statement
from typing import Optional
import datetime
from typing import List, Dict
from .reasoning import explain_client_profile
from .allocation_reasoning import derive_strategic_asset_allocation
from .models import (
    ClientSnapshot,
    ClientRecord,
    SessionLocal,
    ClientBackground,
    Goal,
    ReturnObjective,
    RiskTolerance,
    FinancialNeeds,
    TimeHorizon,
    TaxProfile,
    ReviewProcess,
)


DEFAULT_GOAL_INFLATION = 0.07  # 7%, or later load from env

# =========================
# DB helpers
# =========================

# Purpose: Persist a ClientSnapshot to the database
# Denormalized fields: Key fields (client_name, overall_risk) are stored in columns for fast filtering
# Full snapshot: Complete data stored as JSON in payload_json column
# Safe cleanup: finally block ensures session closes even on errors
# Return ID: Useful for linking related records or generating URLs

def save_client_to_db(snapshot: ClientSnapshot) -> int:
    session = SessionLocal()
    try:
        record = ClientRecord(
            client_name=snapshot.background.client_name,
            occupation=snapshot.background.occupation or "",
            primary_objective=snapshot.return_objective.primary_objectives,
            overall_risk=snapshot.risk_tolerance.overall_risk_tolerance or "",
            currency=snapshot.return_objective.currency or "INR",
            payload_json=snapshot.model_dump_json(),
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        return record.id
    finally:
        session.close()

# Purpose: Retrieve all clients, newest first
# Ordered results: Most recent clients appear first
# Returns ORM objects: Need to deserialize payload_json to get full ClientSnapshot

def load_all_clients() -> List[ClientRecord]:
    session = SessionLocal()
    try:
        return session.query(ClientRecord).order_by(ClientRecord.created_at.desc()).all()
    finally:
        session.close()


def load_client_by_id(client_id: int) -> Optional[ClientRecord]:
    session = SessionLocal()
    try:
        return session.query(ClientRecord).filter_by(id=client_id).first()
    finally:
        session.close()


# =========================
# Cash flow & balance sheet
# =========================

# Generate a financial snapshot at a point in time
def generate_balance_sheet(snapshot: ClientSnapshot) -> Dict:
    assets = {
        "mutual_funds": snapshot.total_mutual_funds or 0.0,
        "equities": snapshot.total_equities or 0.0,
        "debt": snapshot.total_debt or 0.0,
        "cash_bank": snapshot.total_cash_bank or 0.0,
        "properties": snapshot.properties_value or 0.0,
    }
    liabilities = {
        "non_mortgage_loans": snapshot.total_liabilities or 0.0,
        "mortgage": snapshot.mortgage_balance or 0.0,
    }
    total_assets = sum(assets.values())
    total_liabilities = sum(liabilities.values())
    net_worth = total_assets - total_liabilities
    return {
        "assets": assets,
        "liabilities": liabilities,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "net_worth": net_worth,
    }


# =========================
# Snapshot builder
# =========================


# This is the most complex function - it transforms a flat dictionary (from conversation state) into a structured ClientSnapshot.
# Purpose: Convert conversational data into a validated domain model

def build_snapshot_from_state(state: Dict) -> ClientSnapshot:  #Output: Nested ClientSnapshot object
    # Build goals_list from flat state
    goals_list: List[Goal] = []
    for raw_goal in (state.get("goals") or []):
        g = Goal(
            description=raw_goal["description"],
            target_year=raw_goal["target_year"],
            goal_type=raw_goal.get("goal_type", "expense"),
            amount=raw_goal.get("amount"),
            inflation_rate=raw_goal.get("inflation_rate") or DEFAULT_GOAL_INFLATION,
        )
        goals_list.append(g)
    # Provides default for required client_name field. Other fields can be None
    background = ClientBackground(
        client_name=state.get("background.client_name", "Unknown client"),
        occupation=state.get("background.occupation"),
        family_details=state.get("background.family_details"),
        wealth_source=state.get("background.wealth_source"),
        core_values=state.get("background.core_values"),
    )

    _po = state.get("return_objective.primary_objectives")
    _valid_objectives = ("growth", "income", "retirement", "expense")
    primary_obj = _po if _po in _valid_objectives else "growth"

    # Build ReturnObjective (with percentage conversion). Convert percentage to decimal.
    ro = ReturnObjective(
        primary_objectives=primary_obj,
        description=state.get("return_objective.description"),
        required_rate_of_return=(
            (state.get("return_objective.required_rate_of_return") or 0) / 100.0
            if state.get("return_objective.required_rate_of_return") not in [None]
            else None
        ),
        income_requirement=state.get("return_objective.income_requirement"),
        currency=state.get("return_objective.currency") or "INR",
    )
   
    # Straightforward mapping from flat keys
    rt = RiskTolerance(
        overall_risk_tolerance=state.get("risk_tolerance.overall_risk_tolerance"),
        ability_to_take_risk=state.get("risk_tolerance.ability_to_take_risk"),
        willingness_to_take_risk=state.get("risk_tolerance.willingness_to_take_risk"),
        ability_drivers=state.get("risk_tolerance.ability_drivers"),
        willingness_drivers=state.get("risk_tolerance.willingness_drivers"),
    )

    #Build FinancialNeeds (with calculated investible assets). Calculated Field: investible_assets is derived from sum of liquid holdings
    # Single goal → is_multi_stage=False, Multiple goals → is_multi_stage=True, Horizon calculated from furthest goal year 
    fn = FinancialNeeds(
        investible_assets=(
            (state.get("total_mutual_funds") or 0)
            + (state.get("total_equities") or 0)
            + (state.get("total_debt") or 0)
            + (state.get("total_cash_bank") or 0)
        ),
        liabilities=state.get("total_liabilities"),
        properties=state.get("properties_value"),
        mortgage=state.get("mortgage_balance"),
        expected_inflows=state.get("financial_needs.expected_inflows"),
        regular_outflows=state.get("financial_needs.regular_outflows"),
        planned_large_outflows=state.get("financial_needs.planned_large_outflows"),
        emergency_fund_requirement=state.get("financial_needs.emergency_fund_requirement"),
        liquidity_timeframe=state.get("financial_needs.liquidity_timeframe"),
    )

    current_year = datetime.datetime.today().year
    if goals_list:
        max_year = max(g.target_year for g in goals_list)
        total_h = max_year - current_year
    else:
        total_h = None

    th = TimeHorizon(
        is_multi_stage=len(goals_list) > 1,
        total_horizon_years=total_h,
        stages_description="; ".join([f"{g.description} by {g.target_year}" for g in goals_list]) if goals_list else None,
    )

    # Build TaxProfile (with percentage conversion)
    tp = TaxProfile(
        current_incometax_rate=(
            (state.get("tax_profile.current_incometax_rate") or 0) / 100.0
            if state.get("tax_profile.current_incometax_rate") not in [None]
            else None
        ),
        current_capitalgainstax_rate=(
            (state.get("tax_profile.current_capitalgainstax_rate") or 0) / 100.0
            if state.get("tax_profile.current_capitalgainstax_rate") not in [None]
            else None
        ),
        tax_notes=state.get("tax_profile.tax_notes"),
    )

    # Build ReviewProcess
    rp = ReviewProcess(
        meeting_frequency=state.get("review_process.meeting_frequency"),
        review_triggers=state.get("review_process.review_triggers"),
        update_process=state.get("review_process.update_process"),
    )
    existing_positions_raw = state.get("existing_positions_raw")
    existing_positions_parsed = (
        parse_statement(existing_positions_raw) if existing_positions_raw else None
    )

    # Assemble Final Snapshot
    snapshot = ClientSnapshot(
        background=background,
        goals=goals_list,
        return_objective=ro,
        risk_tolerance=rt,
        financial_needs=fn,
        tax_profile=tp,
        time_horizon=th,
        review_process=rp,
        strategic_asset_allocation=None,
        profile_summary=None,
        risk_return_assessment=None,
        goals_alignment_assessment=None,
        existing_positions_raw=existing_positions_raw,
        existing_positions=existing_positions_parsed,

        # Projection assumptions
        current_fy=state.get("current_fy") or current_year,
        income_growth_rate=state.get("income_growth_rate") or 0.0,
        expense_growth_rate=state.get("expense_growth_rate") or 0.0,
        roi_rate=state.get("roi_rate") or (ro.required_rate_of_return or 0.0),
        tax_rate=(state.get("overall_tax_rate") or 0.0) / 100.0
        if state.get("overall_tax_rate") is not None
        else None,

        # Cash-flow base
        annual_income=state.get("annual_income"),
        annual_expenses=state.get("annual_expenses"),
        one_off_future_expenses=state.get("one_off_future_expenses") or [],
        one_off_future_inflows=state.get("one_off_future_inflows") or [],

        # Balance-sheet
        total_mutual_funds=state.get("total_mutual_funds"),
        total_equities=state.get("total_equities"),
        total_debt=state.get("total_debt"),
        total_cash_bank=state.get("total_cash_bank"),
        total_liabilities=state.get("total_liabilities"),
        properties_value=state.get("properties_value"),

        # Mortgage
        mortgage_balance=state.get("mortgage_balance"),
        mortgage_emi=state.get("mortgage_emi"),
        mortgage_interest_rate=(
            (state.get("mortgage_interest_rate") or 0.0) / 100.0
            if state.get("mortgage_interest_rate") is not None
            else None
        ),
    )

    explanations = explain_client_profile(snapshot)
    snapshot.profile_summary = explanations["profile_summary"]
    snapshot.risk_return_assessment = explanations["risk_return_assessment"]
    snapshot.goals_alignment_assessment = explanations["goals_alignment_assessment"]
    
    # LLM-based asset allocation with guardrails
    try:
        saa, rationale = derive_strategic_asset_allocation(snapshot)
        snapshot.strategic_asset_allocation = saa
        snapshot.asset_allocation_rationale = rationale
    except Exception as e:
        snapshot.strategic_asset_allocation = None
        snapshot.saa_error = f"Error deriving strategic asset allocation: {str(e)}"
    return snapshot
