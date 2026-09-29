"""The engine's debt-netting pool and the app's short-term holding set are both
derived from the classifier's Debt subgroups; they must not drift."""


def test_netting_pool_is_every_debt_subgroup():
    from app.domains.ai_engine.common import ensure_ai_agents_path
    from app.domains.mutual_funds.services.scheme_classification import (
        ASSET_CLASS_DEBT,
        SHORT_TERM_HOLDING_SUBGROUPS,
        SUBGROUP_TO_ASSET_CLASS,
    )

    ensure_ai_agents_path()
    from Rebalancing.tables import DEBT_NETTING_POOL

    debt = {sg for sg, ac in SUBGROUP_TO_ASSET_CLASS.items() if ac == ASSET_CLASS_DEBT}
    assert set(DEBT_NETTING_POOL) == debt
    assert SHORT_TERM_HOLDING_SUBGROUPS == DEBT_NETTING_POOL - {"arbitrage_plus_income"}
