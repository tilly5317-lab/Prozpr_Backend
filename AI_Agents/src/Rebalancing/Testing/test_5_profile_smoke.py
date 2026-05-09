from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from asset_allocation_pydantic import run_allocation

from Rebalancing import run_rebalancing
from Rebalancing.Testing.Master_testing.bridge import (
    build_request,
    load_ranking,
    rank1_lookup,
)
from Rebalancing.Testing.Master_testing.profiles import (
    BAD_ISIN,
    PROFILES,
    synth_holdings,
)


_RANKING_CSV = (
    Path(__file__).resolve().parents[3]
    / "Reference_docs" / "Prozpr_fund_ranking.csv"
)


@pytest.fixture(scope="module")
def ranking():
    return load_ranking(_RANKING_CSV)


@pytest.fixture(params=list(PROFILES.keys()))
def profile_name(request):
    return request.param


def test_profile_runs_end_to_end(profile_name, ranking):
    profile = PROFILES[profile_name]
    alloc_out = run_allocation(profile)
    holdings = synth_holdings(profile, alloc_out, rank1_lookup(ranking))
    request = build_request(profile, alloc_out, holdings, ranking)
    response = run_rebalancing(request)

    # Holdings sum to corpus (closed system at the input).
    assert (
        sum(h.present_inr for h in holdings)
        == Decimal(str(profile.total_corpus))
    )

    # Closed system at output: buys can't exceed available sell cash.
    assert response.totals.total_buy_inr <= response.totals.total_sell_inr

    # No row goes negative through rebalancing.
    for r in response.rows:
        assert r.holding_after_initial_trades >= Decimal(0), (
            f"{profile_name} {r.isin}: allocation_5={r.holding_after_initial_trades}"
        )

    # The injected BAD fund must show up as an EXIT.
    bad_trade = next(
        (t for t in response.trade_list if t.isin == BAD_ISIN),
        None,
    )
    assert bad_trade is not None, f"{profile_name}: BAD fund missing from trades"
    assert bad_trade.action == "EXIT"
    assert bad_trade.reason_code == "exit_bad_fund"

    # Engine must produce at least one BUY (drift recipe guarantees buy demand).
    assert any(t.action == "BUY" for t in response.trade_list), (
        f"{profile_name}: no BUY trades — drift recipe failed to fire"
    )
