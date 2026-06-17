"""Importer that loads every ORM model so Alembic + ``Base.metadata`` see them.

After the DDD refactor each domain owns its own ``models/`` package. There is
no longer a single ``app/models/`` aggregator. Alembic's ``env.py`` imports
this module instead, so every ``__tablename__`` registers with
``Base.metadata`` before ``--autogenerate`` runs.

Adding a new domain or model: extend the relevant import below.
"""

# noqa: F401 — these imports exist purely for their registration side-effect.

from app.domains.identity.models import (  # noqa: F401
    user,
    family_member,
    linked_account,
)
from app.domains.profile.models import (  # noqa: F401
    personal_finance_profile,
    risk_profile,
    effective_risk_assessment,
    investment_profile,
    investment_constraint,
    asset_allocation_constraint,
    tax_profile,
    review_preference,
    other_investment,
    user_current_property,
)
from app.domains.goals.models import (  # noqa: F401
    enums,
    financial_goal,
    goal_contribution,
    goal_holding,
)
from app.domains.portfolio.models import (  # noqa: F401
    portfolio,
    user_portfolio_nav_history,
    portfolio_networth_job,
)
from app.domains.mutual_funds.models import (  # noqa: F401
    enums as mf_enums,
    fund,
    mf_aa_import,
    mf_fund_metadata,
    mf_fund_rating,
    mf_nav_history,
    mf_sip_mandate,
    mf_transaction,
    mf_allocation_snapshot,
    user_investment_list,
    user_mf_latest_snapshot,
)
from app.domains.benchmarks.models import (  # noqa: F401
    benchmark_index,
    benchmark_index_value,
)
from app.domains.equities.models import (  # noqa: F401
    enums as equities_enums,
    company_metadata,
    equity_price_history,
    equity_transaction,
)
from app.domains.asset_allocation.models import (  # noqa: F401
    bucket,
    run,
)
from app.domains.practical_asset_allocation.models import (  # noqa: F401
    run as practical_run,
)
from app.domains.rebalancing.models import (  # noqa: F401
    rebalancing_run,
    rebalancing_fund_row,
    rebalancing_subgroup_summary,
    rebalancing_trade,
    rebalancing_warning,
)
from app.domains.cashflow.models import (  # noqa: F401
    enums as cashflow_enums,
    assumptions,
    one_off_event,
    plan_run,
)
from app.domains.advisory.models import ips, meeting_note  # noqa: F401
from app.domains.notifications.models import notification  # noqa: F401
from app.domains.chat.models import (  # noqa: F401
    chat,
    chat_session_state,
    chat_ai_module_run,
)
