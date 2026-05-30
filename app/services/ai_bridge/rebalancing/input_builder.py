"""Materialise a RebalancingComputeRequest from TurnContext + allocation output + DB."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.mf.mf_fund_metadata import MfFundMetadata
from app.models.mf.mf_nav_history import MfNavHistory
from app.models.profile.tax_profile import TaxProfile
from app.models.stocks.enums import StockTransactionType
from app.models.stocks.stock_transaction import StockTransaction
from app.services.ai_bridge.common import ensure_ai_agents_path
from app.services.ai_bridge.rebalancing.overrides import effective_param

if TYPE_CHECKING:
    from app.services.chat_core.turn_context import TurnContext
from app.services.ai_bridge.rebalancing.fund_rank import (
    NOT_EVALUATED_REASON,
    FundRankRow,
    get_fund_ranking,
    get_rejection_reasons,
)
from app.services.ai_bridge.rebalancing.holdings_ledger import (
    HoldingLedgerEntry,
    build_holdings_ledger,
)
from app.services.ai_bridge.rebalancing.tax_aging import (
    LotSplit,
    classify_lots_st_lt,
)

ensure_ai_agents_path()

from asset_allocation_pydantic.models import (  # type: ignore[import-not-found]  # noqa: E402
    GoalAllocationOutput,
)
from practical_asset_allocation.pipeline import (  # type: ignore[import-not-found]  # noqa: E402
    PracticalAllocationInput,
)
from Rebalancing.models import (  # type: ignore[import-not-found]  # noqa: E402
    FundRowInput,
    RebalancingComputeRequest,
)


_DEFAULT_TAX_REGIME = "new"
_DEFAULT_TAX_RATE_PCT = 30.0
_DEFAULT_FUND_RATING = 10
_ROUNDING_STEP = 100


class _Unpriceable(Exception):
    """Raised when a recommended ISIN has no NAV available."""


def _rating_field(meta: MfFundMetadata | None, attr: str, default=None):
    """Safely access an attribute on meta's related MfFundRating."""
    if meta is None:
        return default
    rating = getattr(meta, "rating", None)
    if rating is None:
        return default
    return getattr(rating, attr, default)


async def _latest_nav_by_isin(
    db: AsyncSession, isins: set[str],
) -> dict[str, Decimal]:
    if not isins:
        return {}
    rows = (await db.execute(
        select(MfNavHistory.isin, MfNavHistory.nav, MfNavHistory.nav_date)
        .where(MfNavHistory.isin.in_(isins))
        .order_by(MfNavHistory.isin, MfNavHistory.nav_date.desc())
    )).all()
    out: dict[str, Decimal] = {}
    for isin, nav, _date in rows:
        out.setdefault(isin, Decimal(str(nav)))
    return out


async def _metadata_by_isin(
    db: AsyncSession, isins: set[str],
) -> dict[str, MfFundMetadata]:
    if not isins:
        return {}
    rows = (await db.execute(
        select(MfFundMetadata, MfNavHistory.isin)
        .join(MfNavHistory, MfNavHistory.scheme_code == MfFundMetadata.scheme_code)
        .options(selectinload(MfFundMetadata.rating))
        .where(MfNavHistory.isin.in_(isins))
        .distinct()
    )).all()
    return {isin: meta for meta, isin in rows}


async def _sum_non_mf_equity(db: "AsyncSession", *, user_id: "uuid.UUID") -> Decimal:
    """Signed cost basis from StockTransaction: sum(amount where BUY) − sum(amount where SELL).

    Mark-to-market price is out of scope for v1 — uses cost basis (StockTransaction.amount),
    matching how MfTransaction-derived MF rows are currently summed. Replace with a
    current-price multiplication once StockPriceHistory is reliably fresh.
    """
    rows = (await db.execute(
        select(StockTransaction.transaction_type, StockTransaction.amount)
        .where(StockTransaction.user_id == user_id)
    )).all()
    total = Decimal(0)
    for ttype, amt in rows:
        sign = Decimal(1) if ttype == StockTransactionType.BUY else Decimal(-1)
        total += sign * Decimal(str(amt))
    return max(total, Decimal(0))


def _is_elss_row(row: FundRowInput) -> bool:
    return row.asset_subgroup == "tax_efficient_equities"


def _resolve_tax_inputs(tax_profile: Optional[TaxProfile]) -> dict[str, Any]:
    if tax_profile is None:
        return {
            "tax_regime": _DEFAULT_TAX_REGIME,
            "effective_tax_rate_pct": _DEFAULT_TAX_RATE_PCT,
            "carryforward_st_loss_inr": Decimal(0),
            "carryforward_lt_loss_inr": Decimal(0),
        }
    return {
        "tax_regime": tax_profile.tax_regime or _DEFAULT_TAX_REGIME,
        "effective_tax_rate_pct": float(
            tax_profile.income_tax_rate or _DEFAULT_TAX_RATE_PCT
        ),
        "carryforward_st_loss_inr": Decimal(
            str(tax_profile.carryforward_st_loss_inr or 0)
        ),
        "carryforward_lt_loss_inr": Decimal(
            str(tax_profile.carryforward_lt_loss_inr or 0)
        ),
    }


def _build_row(
    *,
    rank_row: Optional[FundRankRow],
    held_entry: Optional[HoldingLedgerEntry],
    target_amount_pre_cap: Decimal,
    current_nav: Decimal,
    asset_class: str,
    is_recommended: bool,
    fund_rating: int,
    asof: date,
    bad_subgroup: Optional[str] = None,
    bad_sub_category: Optional[str] = None,
    bad_fund_name: Optional[str] = None,
    bad_isin: Optional[str] = None,
    selection_reason: Optional[str] = None,
    rejection_reason: Optional[str] = None,
) -> FundRowInput:
    if rank_row is not None:
        subgroup = rank_row.asset_subgroup
        sub_category = rank_row.sub_category
        fund_name = rank_row.fund_name
        isin = rank_row.isin
        rank = rank_row.rank
    else:
        subgroup = bad_subgroup or "unknown"
        sub_category = bad_sub_category or "unknown"
        fund_name = bad_fund_name or "unknown"
        isin = bad_isin or ""
        rank = 0

    if held_entry is not None:
        split: LotSplit = classify_lots_st_lt(
            held_entry.lots,
            asset_class=asset_class,
            current_nav=current_nav,
            as_of=asof,
        )
        present = split.st_value_inr + split.lt_value_inr
        invested = split.st_cost_inr + split.lt_cost_inr
    else:
        split = LotSplit(Decimal(0), Decimal(0), Decimal(0), Decimal(0))
        present = Decimal(0)
        invested = Decimal(0)

    return FundRowInput(
        asset_subgroup=subgroup,
        sub_category=sub_category,
        recommended_fund=fund_name,
        isin=isin,
        rank=rank,
        target_amount_pre_cap=target_amount_pre_cap,
        present_allocation_inr=present,
        invested_cost_inr=invested,
        st_value_inr=split.st_value_inr,
        st_cost_inr=split.st_cost_inr,
        lt_value_inr=split.lt_value_inr,
        lt_cost_inr=split.lt_cost_inr,
        current_nav=current_nav,
        fund_rating=fund_rating,
        is_recommended=is_recommended,
        selection_reason=selection_reason,
        rejection_reason=rejection_reason,
    )


async def build_rebalancing_input_for_user(
    ctx: "TurnContext",
    allocation_output: Any,
) -> tuple[RebalancingComputeRequest, dict[str, Any]]:
    """Return ``(request, debug_dict)`` for ``run_rebalancing(...)``."""
    user = ctx.user_ctx
    db = ctx.db
    asof = date.today()

    # 1. Holdings ledger.
    ledger = await build_holdings_ledger(db, user_id=user.id)
    held_by_isin: dict[str, HoldingLedgerEntry] = {e.isin: e for e in ledger}

    # 2. Sub-asset-group targets from allocation.
    target_by_subgroup: dict[str, Decimal] = {}
    for r in allocation_output.aggregated_subgroups:
        target_by_subgroup[r.subgroup] = Decimal(str(r.total))

    # 3. Fund-rank table + per-ISIN rejection reasons for off-list held funds.
    ranking = get_fund_ranking()
    rejection_reasons = get_rejection_reasons()
    recommended_isins: set[str] = {
        rr.isin for rows in ranking.values() for rr in rows
    }

    # 4. Bulk-fetch NAV + metadata for everything we need.
    held_isins = set(held_by_isin)
    all_isins = recommended_isins | held_isins
    nav_by_isin = await _latest_nav_by_isin(db, all_isins)
    meta_by_isin = await _metadata_by_isin(db, all_isins)

    rows: list[FundRowInput] = []
    seen_isins: set[str] = set()
    subgroups_applied_rank1: set[str] = set()

    # 5. Recommended-fund rows.
    for subgroup, rank_rows in ranking.items():
        rank1_target = target_by_subgroup.get(subgroup, Decimal(0))
        for rr in rank_rows:
            held = held_by_isin.get(rr.isin)
            current_nav = nav_by_isin.get(rr.isin)
            if current_nav is None:
                if held is None:
                    raise _Unpriceable(
                        f"recommended ISIN {rr.isin} ({rr.fund_name}) has no NAV"
                    )
                # Fallback for held ISIN: latest acquisition_nav as conservative price.
                current_nav = held.lots[-1].acquisition_nav

            meta = meta_by_isin.get(rr.isin)
            asset_class = (meta.asset_class if meta else None) or "equity"

            rows.append(_build_row(
                rank_row=rr,
                held_entry=held,
                target_amount_pre_cap=rank1_target if rr.rank == 1 else Decimal(0),
                current_nav=current_nav,
                asset_class=asset_class,
                is_recommended=True,
                fund_rating=_DEFAULT_FUND_RATING,
                asof=asof,
                selection_reason=rr.selection_reason or None,
            ))
            seen_isins.add(rr.isin)
            if rr.rank == 1 and rank1_target > 0:
                subgroups_applied_rank1.add(subgroup)

    # 5b. Missing or sparse CSV: attach rank-1 targets to the first held fund per
    # subgroup that still has a positive allocation target but no rank-1 row.
    for subgroup, rank1_target in target_by_subgroup.items():
        if rank1_target <= 0 or subgroup in subgroups_applied_rank1:
            continue
        for isin, held in held_by_isin.items():
            if isin in seen_isins:
                continue
            meta = meta_by_isin.get(isin)
            meta_sg = _rating_field(meta, "asset_subgroup") or ""
            if meta_sg != subgroup:
                continue
            current_nav = nav_by_isin.get(isin)
            if current_nav is None:
                current_nav = held.lots[-1].acquisition_nav
            asset_class = _rating_field(meta, "asset_class") or "equity"
            exit_load_pct = float(_rating_field(meta, "exit_load_percent") or 0.0)
            exit_load_months = int(_rating_field(meta, "exit_load_months") or 0)
            rr = FundRankRow(
                asset_subgroup=subgroup,
                sub_category=(meta.sub_category or "unknown") if meta else "unknown",
                rank=1,
                isin=isin,
                fund_name=(meta.scheme_name or isin) if meta else isin,
            )
            rows.append(_build_row(
                rank_row=rr,
                held_entry=held,
                target_amount_pre_cap=rank1_target,
                current_nav=current_nav,
                asset_class=asset_class,
                exit_load_pct=exit_load_pct,
                exit_load_months=exit_load_months,
                is_recommended=True,
                fund_rating=_DEFAULT_FUND_RATING,
                asof=asof,
            ))
            seen_isins.add(isin)
            subgroups_applied_rank1.add(subgroup)
            break

    # 6. BAD-fund rows.
    bad_count = 0
    for isin, entry in held_by_isin.items():
        if isin in seen_isins:
            continue
        meta = meta_by_isin.get(isin)
        current_nav = nav_by_isin.get(isin) or entry.lots[-1].acquisition_nav
        asset_class = _rating_field(meta, "asset_class") or "equity"
        rows.append(_build_row(
            rank_row=None,
            held_entry=entry,
            target_amount_pre_cap=Decimal(0),
            current_nav=current_nav,
            asset_class=asset_class,
            is_recommended=False,
            fund_rating=_DEFAULT_FUND_RATING,
            asof=asof,
            bad_subgroup=(_rating_field(meta, "asset_subgroup") or "unknown"),
            bad_sub_category=(meta.sub_category if meta else "unknown"),
            bad_fund_name=(meta.scheme_name if meta else entry.scheme_code),
            bad_isin=isin,
            rejection_reason=rejection_reasons.get(isin) or NOT_EVALUATED_REASON,
        ))
        bad_count += 1

    # 7. Partition rows: ELSS becomes a scalar (practical_allocation_input.elss_corpus);
    #    the rest stays as the engine's per-fund row list.
    elss_rows = [r for r in rows if _is_elss_row(r)]
    mf_rows_only = [r for r in rows if not _is_elss_row(r)]

    # Builder no longer pre-assigns targets — the engine pipeline lifts them
    # from practical.aggregated_subgroups onto rank-1 rows post-step0.
    mf_rows_only = [
        r.model_copy(update={"target_amount_pre_cap": Decimal(0)})
        for r in mf_rows_only
    ]

    # 8. Corpus scalars for the practical engine.
    mf_corpus_inr = sum(
        (r.present_allocation_inr for r in rows if r.present_allocation_inr > 0),
        start=Decimal(0),
    )
    elss_corpus_inr = sum(
        (r.present_allocation_inr for r in elss_rows),
        start=Decimal(0),
    )
    non_mf_equity_corpus_inr = await _sum_non_mf_equity(db, user_id=user.id)
    # TODO(cash): cash holdings (SB account, FD, RD, liquid scheme outside MF)
    # are not yet aggregated by a dedicated table. Treat as 0 for v1; revisit
    # once a cash-holdings source exists. This means total_corpus here is
    # MF + non-MF equity only, which matches the current behaviour of the
    # legacy `total_corpus = sum(present)` line replaced above for MF-only users.
    cash_inr = Decimal(0)
    total_corpus_inr = mf_corpus_inr + non_mf_equity_corpus_inr + cash_inr

    # 9. Build PracticalAllocationInput.
    practical_input = _build_practical_input(
        allocation_output=allocation_output,
        total_corpus_inr=total_corpus_inr,
        mf_corpus_inr=mf_corpus_inr,
        non_mf_equity_corpus_inr=non_mf_equity_corpus_inr,
        elss_corpus_inr=elss_corpus_inr,
    )

    # 10. Tax inputs. Query directly — relationship may not be eager-loaded.
    tax_profile = (await db.execute(
        select(TaxProfile).where(TaxProfile.user_id == user.id)
    )).scalar_one_or_none()
    tax_inputs = _resolve_tax_inputs(tax_profile)

    # 8b. Optional counterfactual-explore overrides — read from ctx.chat_overrides.
    tax_rate_override = effective_param(ctx, "effective_tax_rate", None)
    stcg_budget_override = effective_param(ctx, "stcg_offset_budget_inr", None)
    carryforward_st_override = effective_param(ctx, "carryforward_st_loss_inr", None)
    carryforward_lt_override = effective_param(ctx, "carryforward_lt_loss_inr", None)

    request = RebalancingComputeRequest(
        practical_allocation_input=practical_input,
        tax_regime=tax_inputs["tax_regime"],
        effective_tax_rate_pct=(
            float(tax_rate_override) if tax_rate_override is not None
            else tax_inputs["effective_tax_rate_pct"]
        ),
        rounding_step=_ROUNDING_STEP,
        stcg_offset_budget_inr=(
            Decimal(str(stcg_budget_override))
            if stcg_budget_override is not None else None
        ),
        carryforward_st_loss_inr=(
            Decimal(str(carryforward_st_override))
            if carryforward_st_override is not None
            else tax_inputs["carryforward_st_loss_inr"]
        ),
        carryforward_lt_loss_inr=(
            Decimal(str(carryforward_lt_override))
            if carryforward_lt_override is not None
            else tax_inputs["carryforward_lt_loss_inr"]
        ),
        rows=mf_rows_only,
    )
    debug = {
        "total_corpus": str(total_corpus_inr),
        "mf_corpus": str(mf_corpus_inr),
        "elss_corpus": str(elss_corpus_inr),
        "non_mf_equity_corpus": str(non_mf_equity_corpus_inr),
        "lots_per_isin": {e.isin: len(e.lots) for e in ledger},
        "bad_fund_count": bad_count,
        "row_count": len(mf_rows_only),
    }
    return request, debug


def _build_practical_input(
    *,
    allocation_output: GoalAllocationOutput,
    total_corpus_inr: Decimal,
    mf_corpus_inr: Decimal,
    non_mf_equity_corpus_inr: Decimal,
    elss_corpus_inr: Decimal,
) -> "PracticalAllocationInput":
    """Bridge-side PracticalAllocationInput builder.

    Reuses everything we know from the upstream allocation output's
    `client_summary` (profile, goals) and falls back to safe defaults for
    fields the bridge doesn't currently surface (market_commentary,
    multi_asset_composition, advisor cap override). Once Plan B's
    `app/services/ai_bridge/practical_allocation/input_builder.py` lands,
    delegate to it and delete this thin shim.
    """
    cs = allocation_output.client_summary
    return PracticalAllocationInput(
        # Profile + goals carried from the ideal-allocation output.
        effective_risk_score=cs.effective_risk_score,
        age=cs.age,
        annual_income=getattr(cs, "annual_income", 0) or 0,
        osi=getattr(cs, "osi", 0.0) or 0.0,
        savings_rate_adjustment=getattr(cs, "savings_rate_adjustment", "none") or "none",
        gap_exceeds_3=False,
        shortfall_amount=0.0,
        total_corpus=float(total_corpus_inr),
        monthly_household_expense=getattr(cs, "monthly_household_expense", 0) or 0,
        effective_tax_rate=15.0,  # overridden downstream by TaxProfile
        net_financial_assets=float(total_corpus_inr),
        goals=list(cs.goals),
        # Four NEW corpus scalars.
        mf_corpus=float(mf_corpus_inr),
        non_mf_equity_corpus=float(non_mf_equity_corpus_inr),
        elss_corpus=float(elss_corpus_inr),
        # Optional advisor override left unset.
        max_non_mf_equity_pct_client_input=None,
    )
