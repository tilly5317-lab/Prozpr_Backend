"""Materialise a RebalancingComputeRequest from TurnContext + allocation output + DB."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domains.mutual_funds.models.mf_fund_metadata import MfFundMetadata
from app.domains.mutual_funds.models.mf_nav_history import MfNavHistory
from app.domains.profile.models.tax_profile import TaxProfile
from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.practical_asset_allocation.services.paa_engine.input_builder import (
    build_practical_allocation_input_for_user,
)
from app.domains.rebalancing.services.rebal_engine.overrides import effective_param

if TYPE_CHECKING:
    from app.domains.ai_engine.turn_context import TurnContext
from app.domains.rebalancing.services.rebal_engine.fund_rank import (
    FORCE_EXIT_RANK,
    FundRankRow,
    get_force_exit_isins,
    get_fund_ranking,
    get_rejection_reasons,
)
from app.domains.rebalancing.services.rebal_engine.holdings_ledger import (
    HoldingLedgerEntry,
    build_holdings_ledger,
)
from app.domains.rebalancing.services.rebal_engine.tax_aging import (
    LotSplit,
    classify_lots_st_lt,
)

ensure_ai_agents_path()

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
    bad_rank: int = 0,
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
        rank = bad_rank

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

    # 3. Fund-rank table + force-exit ISIN set + per-ISIN rejection reasons.
    ranking = get_fund_ranking()
    force_exit_isins = get_force_exit_isins()
    rejection_reasons = get_rejection_reasons()
    recommended_isins: set[str] = {
        rr.isin for rows in ranking.values() for rr in rows
    }

    # 4. Bulk-fetch NAV + metadata for everything we need.
    held_isins = set(held_by_isin)
    all_isins = recommended_isins | held_isins
    nav_by_isin = await _latest_nav_by_isin(db, all_isins)
    meta_by_isin = await _metadata_by_isin(db, all_isins)

    # NOTE: the NEUTRAL ST offset against subgroup rank-1 targets lives
    # downstream in `Rebalancing.pipeline._assign_targets_to_rank1`. The
    # pipeline lifts the practical-allocation per-subgroup totals onto
    # rank-1 rows (overwriting whatever the input builder set), so any
    # offset applied here would be discarded. Keeping the rule in one
    # place (the pipeline) avoids the two builders drifting.

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
            asset_class = _rating_field(meta, "asset_class") or "equity"

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
                is_recommended=True,
                fund_rating=_DEFAULT_FUND_RATING,
                asof=asof,
            ))
            seen_isins.add(isin)
            subgroups_applied_rank1.add(subgroup)
            break

    # 6. Off-list held funds: force-exit (rank=9999) or NEUTRAL (rank=0).
    #
    # Force-exit: ISIN appears in the ranking CSV with rank=9999. Target = 0
    # so step2 flags exit and step4 fully liquidates regardless of tax.
    # Rejection text from the CSV's *_reason columns is surfaced on the
    # customer-facing TradeAction.
    #
    # NEUTRAL: ISIN is held but not in the ranking CSV at all (or in the CSV
    # with a blank rank). Target = present so diff = 0 and step2 doesn't
    # trade it. Its value has already offset the subgroup's rank-1 target
    # in section 4b so we don't double-allocate.
    force_exit_count = 0
    neutral_count = 0
    for isin, entry in held_by_isin.items():
        if isin in seen_isins:
            continue
        meta = meta_by_isin.get(isin)
        current_nav = nav_by_isin.get(isin) or entry.lots[-1].acquisition_nav
        asset_class = _rating_field(meta, "asset_class") or "equity"
        bad_subgroup = _rating_field(meta, "asset_subgroup") or "unknown"
        bad_sub_category = (meta.sub_category if meta else "unknown") or "unknown"
        bad_fund_name = (meta.scheme_name if meta else entry.scheme_code) or entry.scheme_code

        if isin in force_exit_isins:
            rows.append(_build_row(
                rank_row=None,
                held_entry=entry,
                target_amount_pre_cap=Decimal(0),
                current_nav=current_nav,
                asset_class=asset_class,
                is_recommended=False,
                fund_rating=_DEFAULT_FUND_RATING,
                asof=asof,
                bad_subgroup=bad_subgroup,
                bad_sub_category=bad_sub_category,
                bad_fund_name=bad_fund_name,
                bad_isin=isin,
                bad_rank=FORCE_EXIT_RANK,
                rejection_reason=rejection_reasons.get(isin),
            ))
            force_exit_count += 1
        else:
            # NEUTRAL: target = ST value (the locked minimum). diff =
            # st - present = -lt, exposing the LT portion as a sellable
            # excess. Step4's optional pool (with `st_available=0`) will
            # tap it only when there's recommended-fund buy demand.
            #
            # We pull the CSV's rejection text when the ISIN appears in
            # the evaluated-and-skipped set so the customer-facing trade
            # carries a fund-specific "why we'd prefer to migrate" note.
            # Genuinely-unknown ISINs (not in the CSV at all) get None
            # — the action-level rationale stands alone.
            split = classify_lots_st_lt(
                entry.lots,
                asset_class=asset_class,
                current_nav=current_nav,
                as_of=asof,
            )
            rows.append(_build_row(
                rank_row=None,
                held_entry=entry,
                target_amount_pre_cap=split.st_value_inr,
                current_nav=current_nav,
                asset_class=asset_class,
                is_recommended=False,
                fund_rating=_DEFAULT_FUND_RATING,
                asof=asof,
                bad_subgroup=bad_subgroup,
                bad_sub_category=bad_sub_category,
                bad_fund_name=bad_fund_name,
                bad_isin=isin,
                bad_rank=0,
                rejection_reason=rejection_reasons.get(isin),
            ))
            neutral_count += 1

    # 7. Total corpus = sum of held market values. Snap down to a multiple of
    #    100 so the practical pipeline's multiple-of-100 invariant holds.
    total_corpus = sum(
        (r.present_allocation_inr for r in rows if r.present_allocation_inr > 0),
        start=Decimal(0),
    )
    total_corpus = Decimal(int(max(total_corpus, Decimal(0)) // 100 * 100))

    # 7b. Practical allocation input — the Rebalancing engine runs the practical
    #     (holdings-aware) allocation internally and lifts its per-subgroup MF
    #     targets onto the rank-1 rows. Build it via the practical_asset_allocation
    #     domain, then point the corpus at the held MF value so the targets sum to
    #     what's actually held (a rebalance, not a fresh cash deployment). Non-MF
    #     equity ("stocks") and ELSS default to 0 — no holdings breakdown wired yet.
    practical_input, _paa_debug = build_practical_allocation_input_for_user(ctx)
    practical_input = practical_input.model_copy(update={
        "total_corpus": float(total_corpus),
        "mf_corpus": float(total_corpus),
    })

    # 8. Tax inputs. Query directly — relationship may not be eager-loaded.
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
        rows=rows,
    )
    debug = {
        "total_corpus": str(total_corpus),
        "lots_per_isin": {e.isin: len(e.lots) for e in ledger},
        "force_exit_count": force_exit_count,
        "neutral_count": neutral_count,
        "row_count": len(rows),
    }
    return request, debug
