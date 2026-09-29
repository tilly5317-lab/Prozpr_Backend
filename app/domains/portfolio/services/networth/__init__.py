"""Per-user daily net-worth history: transactions x units x that day's NAV.

The series behind the portfolio chart, the value build-up waterfall and the Returns tab.
It is derived from two honest sources — the CAS transaction ledger (``mf_transactions``)
and daily NAV (``mf_nav_history``) — and is fully recomputable at any time, which is why
nothing here is CAS-snapshot-versioned.

Layout:

* ``replay``       — the business rules, pure and testable (no DB, no clock, no I/O)
* ``loader``       — the queries that feed the replay
* ``nav_coverage`` — phase A: find and close gaps in NAV history
* ``writer``       — the atomic delete-all-then-insert
* ``builder``      — orchestration: pin CAS scope, run the phases, report progress
* ``job``          — job row lifecycle: single-flight, heartbeat, reaping, supersession
* ``daily``        — the fleet-scale daily refresh (one statement, not a loop per user)
* ``asof``         — value the ledger at one date without building a whole series
* ``clock``        — the one clock: today means today in India

A new CAS upload triggers ``rebuild_user_networth``, which deletes the user's entire
series and recomputes it. That is deliberate — a new statement can change history, not
just extend it — and it is safe because the delete and the insert share one transaction.
"""

from __future__ import annotations

from app.domains.portfolio.services.networth.asof import (  # noqa: F401
    compute_networth_as_of,
    compute_today_networth,
)
from app.domains.portfolio.services.networth.builder import (  # noqa: F401
    build_series,
    rebuild_user_networth,
)
from app.domains.portfolio.services.networth.clock import ist_today  # noqa: F401
from app.domains.portfolio.services.networth.daily import (  # noqa: F401
    run_daily_networth_job,
)
from app.domains.portfolio.services.networth.job import (  # noqa: F401
    create_job,
    get_latest_job,
    has_reapable_job,
    has_running_job,
    reap_stale_jobs,
    request_supersede,
)
from app.domains.portfolio.services.networth.reader import (  # noqa: F401
    HORIZON_DAYS,
    get_series_state,
    get_user_nav_history,
)

__all__ = [
    "HORIZON_DAYS",
    "build_series",
    "compute_networth_as_of",
    "compute_today_networth",
    "create_job",
    "get_latest_job",
    "get_series_state",
    "get_user_nav_history",
    "has_reapable_job",
    "has_running_job",
    "ist_today",
    "reap_stale_jobs",
    "rebuild_user_networth",
    "request_supersede",
    "run_daily_networth_job",
]
