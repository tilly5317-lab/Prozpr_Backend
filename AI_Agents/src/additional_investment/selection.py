from __future__ import annotations

from dataclasses import dataclass

from additional_investment.models import FundBuy, Holding, RankedFund, SubgroupTarget


@dataclass
class _Candidate:
    isin: str
    recommended_fund: str
    sub_category: str
    reason: str


def _round_down(amount: float, multiple: int) -> float:
    if multiple <= 0:
        return amount
    return float(int(amount // multiple) * multiple)


def _cap_amount(subgroup: str, resulting_corpus: float,
                cap_pct_by_subgroup: dict[str, float], default_cap_pct: float) -> float:
    pct = cap_pct_by_subgroup.get(subgroup, default_cap_pct)
    return resulting_corpus * pct / 100.0


def _ordered_candidates(subgroup: str,
                        ranked_by_sg: dict[str, list[RankedFund]],
                        held_by_sg: dict[str, list[Holding]]) -> list[_Candidate]:
    out: list[_Candidate] = []
    seen: set[str] = set()
    # 1) acceptable existing holdings first, biggest position first (consolidate)
    for h in sorted(held_by_sg.get(subgroup, []), key=lambda x: x.present_amount_inr, reverse=True):
        if h.force_exit:
            continue
        acceptable = (h.rank is not None) or (h.rating is not None and h.rating >= 5)
        if not acceptable:
            continue
        out.append(_Candidate(h.isin, h.recommended_fund, h.sub_category,
                              "Top-up of your existing holding in this category"))
        seen.add(h.isin)
    # 2) ranked funds rank-1..N, skipping any already added as a holding
    for f in ranked_by_sg.get(subgroup, []):
        if f.isin in seen:
            continue
        out.append(_Candidate(f.isin, f.recommended_fund, f.sub_category,
                              "Recommended fund for this category"))
        seen.add(f.isin)
    return out


def select_funds(
    targets: list[SubgroupTarget],
    ranked_funds: list[RankedFund],
    holdings: list[Holding],
    resulting_corpus: float,
    cap_pct_by_subgroup: dict[str, float],
    default_cap_pct: float,
    rounding_multiple: int,
) -> list[FundBuy]:
    ranked_by_sg: dict[str, list[RankedFund]] = {}
    for f in ranked_funds:
        ranked_by_sg.setdefault(f.asset_subgroup, []).append(f)
    for fl in ranked_by_sg.values():
        fl.sort(key=lambda x: x.rank)

    held_by_sg: dict[str, list[Holding]] = {}
    for h in holdings:
        held_by_sg.setdefault(h.asset_subgroup, []).append(h)

    buys: list[FundBuy] = []
    for t in targets:
        cap_amt = _cap_amount(t.subgroup, resulting_corpus, cap_pct_by_subgroup, default_cap_pct)
        present_by_isin = {h.isin: h.present_amount_inr for h in held_by_sg.get(t.subgroup, [])}
        bought_by_isin: dict[str, float] = {}
        remaining = t.target_inr
        for cand in _ordered_candidates(t.subgroup, ranked_by_sg, held_by_sg):
            if remaining < rounding_multiple:
                break
            present = present_by_isin.get(cand.isin, 0.0)
            already = bought_by_isin.get(cand.isin, 0.0)
            headroom = cap_amt - present - already
            if headroom < rounding_multiple:
                continue
            buy_amt = _round_down(min(remaining, headroom), rounding_multiple)
            if buy_amt < rounding_multiple:
                continue
            bought_by_isin[cand.isin] = already + buy_amt
            buys.append(FundBuy(
                recommended_fund=cand.recommended_fund,
                isin=cand.isin,
                sub_category=cand.sub_category,
                asset_subgroup=t.subgroup,
                amount_inr=buy_amt,
                reason=cand.reason,
            ))
            remaining -= buy_amt
    return buys
