"""Declarative registry of the Value Research tables we mirror.

One :class:`VrTableSpec` per VR endpoint. Everything downstream is generated
from this registry — the ``vr`` schema DDL (:mod:`.schema`), the incremental
upsert (:mod:`.services.sync_service`), the ``deleted_logs`` consumer, and the
ops endpoints. **Adding a VR table is a spec entry, not new code.**

Column *names* come from ``catalog.json`` (VR's published field reference,
trimmed to these tables) so the mirror can never drift from the documented
contract by a typo. Column *types* are inferred by :func:`infer_column_type`,
which is deliberately conservative: anything not provably numeric, a date, or a
nested object lands in ``TEXT``. A mirror that is lossless beats a mirror that
is prettily typed — we have never seen a live VR response (no key existed as of
2026-08-30), and a wrong ``BIGINT`` guess fails the whole page, while a ``TEXT``
column that turns out to be an integer costs one ``ALTER``.

Tiers mirror the commercial ask, so scope changes are an env var and not a
rewrite:

``core``
    The eight ``plan_id`` tables the CFO listed as required.
``additional``
    ``nav`` — required, listed separately by the CFO.
``optional``
    ``fund_returns_annual`` / ``fund_dividends`` — take only if pricing allows.
``support``
    The short list that genuinely has to ride along with ``core``, established
    against VR's own foreign-key export (``VR_DOCS/api_schema_relation between
    tables.csv``, 273 relations over 216 tables) rather than guessed:

    * ``deleted_logs`` — the deletion feed. Without it, deletions at VR never
      reach the mirror and it silently drifts.
    * ``fund_plans`` — **the finding that changes the ask.** Six of the
      requested tables (``nav``, ``fund_sip_returns``, ``fund_returns_annual``,
      ``fund_dividends``, ``subplan_isin``, ``rta_codes``) foreign-key to
      ``fund_plans.plan_id``, not to ``fund_basic_details``. The two masters
      split the children between them, so taking only one orphans half the feed.
    * ``subplans`` — ``subplan_isin.subplan_code`` and ``rta_codes.subplan_id``
      point here. Without it ``subplan_isin`` is three bare id columns with no
      way to tell Direct-Growth from Regular-IDCW.
    * ``fund_status`` — decodes ``fund_basic_details.status``, the one coded
      field in the whole core set that VR does *not* ship a label beside.

    Everything else that looked like a required master is not one. VR
    denormalises the label next to the id in exactly these tables:
    ``fund_holdings_details`` carries ``security_name``, ``asset_description``,
    ``rating_name`` and ``asset_class``; ``fund_basic_details`` carries a
    ``_name`` for every category, type, AMC and riskometer id;
    ``fund_transaction_details`` carries ``rta_name``. So ``securities``,
    ``instrument``, ``credit_rating_score``, ``sebi_categories``,
    ``fund_categories`` and ``rta_codes`` are **not** needed to read the core
    tables — they become worthwhile later, for joins we do not yet make, and sit
    in ``candidate`` until then.
``candidate``
    Rated highly in the 2026-08-30 evaluation but outside the CFO's list.
    Declared here so a "yes" from VR is a one-line config change; **off by
    default** and never fetched unless a tier is explicitly enabled.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

Tier = Literal["core", "additional", "optional", "support", "candidate"]

CATALOG_PATH = Path(__file__).with_name("catalog.json")

# Tiers fetched when VR_SYNC_TIERS is unset. Candidates and, until the contract
# is signed, optionals stay out.
DEFAULT_ENABLED_TIERS: tuple[Tier, ...] = ("core", "additional", "support")


# ---------------------------------------------------------------------------
# type inference
# ---------------------------------------------------------------------------

#: Fields VR documents as a nested object/array rather than a scalar. They are
#: mirrored as ``jsonb`` verbatim — flattening them would need a second grain
#: (e.g. one row per sector) that VR does not itself expose as a table.
JSON_COLUMNS: frozenset[str] = frozenset(
    {
        "amc_details",
        "benchmark_details",
        "fund_load_cdsc_details",
        "fund_manager_details",
        "holdings_debtstated",
        "holdings_maturity",
        "holdings_rating",
        "latest_sip_details",
        "riskometer_details",
        "sector_details",
        "sip_returns",
        "sip_stp_swp_min_amounts",
        "sip_swp_stp_details",
        "stats_variables",
        "trailing_returns",
    }
)

#: Scalar fields that are unambiguously a calendar date.
DATE_COLUMNS: frozenset[str] = frozenset(
    {
        "allottment_date",
        "as_of",
        "as_on_date",
        "asset_date",
        "date",
        "div_date",
        "isin_as_of_date",
        "issue_actual_close",
        "issue_open",
        "issue_stated_close",
        "last_etf_trade_date",
        "late_redemption",
        "maturity",
        "name_as_of_date",
        "nav_date",
        "portfolio_manager_change_date",
        "rating_date",
        "record_date",
        "resale_start_date",
        "return_date",
    }
)

#: Timestamps (``deleted_logs`` only, today).
TIMESTAMP_COLUMNS: frozenset[str] = frozenset({"deleted_ts", "modified_ts"})

#: Whole numbers we are confident about.
INTEGER_COLUMNS: frozenset[str] = frozenset({"year", "days_diff", "lock_in_period_days"})

#: Exact numeric prefixes/suffixes. Money and percentages are ``NUMERIC`` with
#: no precision so VR can never overflow us — the goals table taught us that.
_NUMERIC_PREFIXES = ("ret_", "rank_", "r1", "r2", "r3", "r4", "r5", "r6", "r9", "corp_")
_NUMERIC_EXACT = frozenset(
    {
        "adjusted_nav",
        "alpha",
        "alpha_stated",
        "annualised_ytm",
        "asset_percentage",
        "asset_value",
        "avg_ytm",
        "beta",
        "beta_stated",
        "comm_max",
        "comm_min",
        "coupon_rate",
        "debt",
        "debt_max",
        "debt_min",
        "commodities",
        "equity",
        "equity_derivatives_max",
        "equity_derivatives_min",
        "equity_max",
        "equity_min",
        "face_value",
        "hedged_equity",
        "information_ratio",
        "large_percentage",
        "latest_aum",
        "latest_expense_ratio",
        "latest_turnover_ratio",
        "market_cap",
        "max_inv_amount",
        "mean",
        "mid_percentage",
        "min_balance",
        "min_initial_investment",
        "min_subsequent_investment",
        "min_subsequent_investment_unit",
        "min_subsequent_sip_investment",
        "min_swp_widw",
        "min_widw_unit",
        "min_withdrawal_multiple_amount",
        "min_withdrawl_amount",
        "money_mkt_max",
        "money_mkt_min",
        "nav",
        "num_of_shares",
        "others",
        "others_excluding_derivatives",
        "pb",
        "pe",
        "percentage",
        "percentage_rs_per_unit",
        "realestate",
        "reit_invit_max",
        "reit_invit_min",
        "returns",
        "rsquare_stated",
        "rsquared",
        "sharpe_ratio",
        "sip_in_multiples_of",
        "sip_max_inv_amount",
        "sip_min_inv_amount",
        "small_percentage",
        "sortino_ratio",
        "standard_deviation",
        "stated_annual_expense",
        "stp_in_multiples_of",
        "stp_min_inv_amount",
        "swp_in_multiples_of",
        "swp_min_inv_amount",
        "treynor",
        "treynor_stated",
    }
)

ColumnType = Literal["text", "numeric", "integer", "date", "timestamptz", "jsonb"]


def infer_column_type(name: str) -> ColumnType:
    """Map a VR field name to a Postgres type. Conservative by design.

    ``*_value`` (the SIP-corpus companions to ``r5year`` etc.) is numeric, but
    ``*_id``, ``*_code`` and every flag stay ``TEXT`` — VR documents them only
    as "unique identifier", and a ``Y``/``N`` or an alphanumeric code arriving
    in a ``BIGINT``/``BOOLEAN`` column fails the entire page, not one field.
    """
    if name in JSON_COLUMNS:
        return "jsonb"
    if name in TIMESTAMP_COLUMNS:
        return "timestamptz"
    if name in DATE_COLUMNS:
        return "date"
    if name in INTEGER_COLUMNS:
        return "integer"
    if name in _NUMERIC_EXACT:
        return "numeric"
    if name.endswith("_value") or name.endswith("_percentage"):
        return "numeric"
    if name.startswith(_NUMERIC_PREFIXES) and not name.endswith(("_name", "_date")):
        return "numeric"
    return "text"


# ---------------------------------------------------------------------------
# spec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VrTableSpec:
    """One mirrored VR table."""

    name: str
    tier: Tier
    primary_key: tuple[str, ...]
    #: Field the ``changed-after`` window walks. ``None`` = no natural date
    #: column, so the table is refreshed whole (all masters are tiny).
    watermark_column: Optional[str] = None
    #: ``"incremental"`` pages ``changed-after``; ``"full"`` re-reads the table.
    sync_mode: Literal["incremental", "full"] = "incremental"
    #: Hours (IST) the scheduler runs this table. Empty = manual/ops only.
    schedule_hours: tuple[int, ...] = ()
    #: Extra indexes beyond the PK, as column tuples.
    indexes: tuple[tuple[str, ...], ...] = ()
    #: Why we take it, for the ops endpoint and the CFO note.
    rationale: str = ""
    description: str = ""
    update_frequency: Optional[str] = None
    columns: tuple[str, ...] = field(default=())

    @property
    def qualified_name(self) -> str:
        return f"vr.{self.name}"

    def column_types(self) -> dict[str, ColumnType]:
        return {c: infer_column_type(c) for c in self.columns}


# Per-table overrides. Columns come from catalog.json; this carries only what
# VR's field reference does not tell us — the key, the cadence, our reason.
_OVERRIDES: dict[str, dict] = {
    # ── core: the eight plan_id tables the CFO listed ────────────────────
    "fund_basic_details": dict(
        tier="core",
        primary_key=("plan_id",),
        sync_mode="incremental",
        schedule_hours=(2,),
        # No isin_code here — fund_basic_details carries amfi_code and
        # nsdl_code but reaches ISIN only through subplan_isin.
        indexes=(("amfi_code",), ("status",), ("sebi_category_id",)),
        rationale=(
            "Master row per plan. 83 fields against mf_fund_metadata's 13 — "
            "adds benchmark, lock-in, minimum investment, SEBI allocation bands "
            "and potential risk class, none of which we hold today."
        ),
    ),
    "subplan_isin": dict(
        tier="core",
        primary_key=("plan_id", "subplan_code", "isin_code"),
        sync_mode="full",
        schedule_hours=(2,),
        indexes=(("isin_code",),),
        rationale=(
            "The ISIN->plan_id crosswalk. CAS ingest already reconciles folio "
            "holdings on ISIN with no reference table behind it; this is what "
            "makes vr.scheme_link possible."
        ),
    ),
    "fund_sip_returns": dict(
        tier="core",
        primary_key=("plan_id", "as_on_date"),
        watermark_column="as_on_date",
        schedule_hours=(3,),
        indexes=(("as_on_date",),),
        rationale=(
            "SIP returns and corpus values per horizon. The SIP tab and goal "
            "planner currently project a monthly contribution on lumpsum-shaped "
            "assumptions."
        ),
    ),
    "fund_performance_details": dict(
        tier="core",
        primary_key=("plan_id", "nav_date"),
        watermark_column="nav_date",
        schedule_hours=(3,),
        indexes=(("nav_date",),),
        rationale=(
            "Daily per-plan performance summary — NAV, 52w high/low, trailing "
            "returns and stats as nested objects. Convenience over "
            "fund_return_latest + stats_variables, not new capability."
        ),
    ),
    "fund_holdings_details": dict(
        tier="core",
        primary_key=("plan_id", "as_on_date", "security_id"),
        watermark_column="as_on_date",
        schedule_hours=(4,),
        indexes=(("as_on_date",), ("security_id",), ("asset_isin",), ("plan_id",)),
        rationale=(
            "Security-level look-through: fund overlap, single-stock "
            "concentration across a client's whole book, true equity exposure "
            "of hybrids. By far the heaviest table here — monthly grain, every "
            "holding of every plan."
        ),
    ),
    "fund_holdings_aggregate_equity": dict(
        tier="core",
        primary_key=("plan_id", "as_on_date"),
        watermark_column="as_on_date",
        schedule_hours=(4,),
        rationale=(
            "Sector totals per plan as one jsonb. Most of the look-through "
            "value without ingesting every security line."
        ),
    ),
    "fund_holdings_aggregate_debt": dict(
        tier="core",
        primary_key=("plan_id", "as_on_date"),
        watermark_column="as_on_date",
        schedule_hours=(4,),
        rationale=(
            "Debt-side equivalent — instrument, maturity and rating breakups. "
            "The input to any credit-quality view of a debt allocation."
        ),
    ),
    "fund_transaction_details": dict(
        tier="core",
        primary_key=("plan_id", "subplan_id"),
        sync_mode="incremental",
        schedule_hours=(2,),
        indexes=(("isin_code",), ("transaction_status",), ("rta_code",)),
        rationale=(
            "Per-subplan transaction facility: scm_pur/sip/red/switch/swp/stp "
            "_available plus minimums and transaction_status. This is the "
            "'is the fund open to subscription' answer — see the freshness "
            "caveat in the CFO note, VR documents it as irregularly updated."
        ),
    ),
    # ── additional ────────────────────────────────────────────────────────
    "nav": dict(
        tier="additional",
        primary_key=("plan_id", "nav_date"),
        watermark_column="nav_date",
        schedule_hours=(1, 22),
        indexes=(("nav_date",),),
        rationale=(
            "The series every downstream number reads. Replaces the mfapi.in "
            "feed behind mf_nav_history and finally gives us adjusted_nav, "
            "which we have never had."
        ),
    ),
    # ── optional (pricing-dependent) ──────────────────────────────────────
    "fund_returns_annual": dict(
        tier="optional",
        primary_key=("plan_id", "year"),
        sync_mode="full",
        indexes=(("year",),),
        rationale=(
            "Calendar-year returns. Small, and the only source for the "
            "'how did it do in 2022/2023' table on a fund page."
        ),
    ),
    "fund_dividends": dict(
        tier="optional",
        primary_key=("plan_id", "div_date", "record_date"),
        watermark_column="div_date",
        indexes=(("div_date",),),
        rationale=(
            "IDCW declarations. Without a dividend calendar a raw NAV series "
            "breaks across every payout, quietly corrupting any return we "
            "compute ourselves."
        ),
    ),
    # ── support: masters + the deletion feed ──────────────────────────────
    "deleted_logs": dict(
        tier="support",
        primary_key=("log_id",),
        watermark_column="deleted_ts",
        schedule_hours=(1, 2, 3, 4, 22),
        indexes=(("table_name",), ("deleted_ts",)),
        rationale=(
            "MANDATORY, not a nice-to-have. Deletions at VR reach the mirror "
            "only through this table; skip it and every mirrored table "
            "accumulates rows VR has already retired, with no error anywhere."
        ),
    ),
    "subplans": dict(
        tier="support",
        primary_key=("plan_id", "subplan_id"),
        sync_mode="full",
        schedule_hours=(2,),
        rationale=(
            "FK parent of subplan_isin.subplan_code and rta_codes.subplan_id. "
            "Without it subplan_isin is three bare id columns with no way to "
            "tell Direct-Growth from Regular-IDCW."
        ),
    ),
    "fund_status": dict(
        tier="support",
        primary_key=("status_id",),
        sync_mode="full",
        rationale=(
            "Decodes fund_basic_details.status. Without it we cannot tell a "
            "live scheme from a merged or wound-up one, and we will recommend "
            "schemes that no longer exist."
        ),
    ),
    "sebi_categories": dict(
        tier="candidate",
        primary_key=("sebi_category_id",),
        sync_mode="full",
        rationale="Decodes the SEBI category ids on fund_basic_details.",
    ),
    "fund_categories": dict(
        tier="candidate",
        primary_key=("category_id",),
        sync_mode="full",
        rationale=(
            "Decodes VR's own category ids — the grain every category average "
            "and rank in the catalogue is keyed on."
        ),
    ),
    "rta_codes": dict(
        tier="candidate",
        primary_key=("plan_id", "subplan_id"),
        sync_mode="full",
        indexes=(("rta_code",),),
        rationale=(
            "RTA code per subplan. The second leg of CAMS reconciliation "
            "alongside subplan_isin."
        ),
    ),
    "securities": dict(
        tier="candidate",
        primary_key=("security_id",),
        sync_mode="incremental",
        indexes=(("isin",), ("entity_id",)),
        rationale=(
            "Security master. Optional until we ingest holdings; the moment "
            "fund_holdings_details lands, its security_id is meaningless "
            "without this."
        ),
    ),
    "credit_rating_score": dict(
        tier="candidate",
        primary_key=("rating_id",),
        sync_mode="full",
        rationale="Decodes rating_id on fund_holdings_details.",
    ),
    "instrument": dict(
        tier="candidate",
        primary_key=("asset_id",),
        sync_mode="full",
        rationale="Decodes asset_id on fund_holdings_details.",
    ),
    "countries": dict(
        tier="candidate",
        primary_key=("iso_country_code",),
        sync_mode="full",
        rationale=(
            "Country master. Joined from companies.country_code / "
            "entity_companies.country_code — not from any plan_id table. "
            "Only earns its place if we analyse international holdings."
        ),
    ),
    # ── candidates: declared, off by default ──────────────────────────────
    "fund_plans": dict(
        tier="support",
        primary_key=("plan_id",),
        sync_mode="incremental",
        schedule_hours=(2,),
        indexes=(("isin_code",), ("amfi_code",), ("category_id",), ("amc_id",)),
        rationale=(
            "Required, not optional, and absent from the requested list. VR's "
            "own FK export has nav, fund_sip_returns, fund_returns_annual, "
            "fund_dividends, subplan_isin and rta_codes all pointing at "
            "fund_plans.plan_id rather than at fund_basic_details — the two "
            "masters split the children between them."
        ),
    ),
    "funds_ratings": dict(
        tier="candidate",
        primary_key=("plan_id", "rating_date"),
        watermark_column="rating_date",
        rationale=(
            "mf_fund_ratings is populated by manual CRUD only, and "
            "rebal_engine/input_builder.py substitutes a default rating of 10 "
            "when a row is missing — so every fund nobody typed in is treated "
            "as well-rated. This is the feed that makes that table real."
        ),
    ),
    "stats_variables": dict(
        tier="candidate",
        primary_key=("plan_id", "as_on_date"),
        watermark_column="as_on_date",
        rationale=(
            "alpha/beta/sharpe/sortino/treynor/stdev/R2. We hold none of them; "
            "the rebalancing engine ranks funds with no risk-adjusted metric."
        ),
    ),
    "fund_return_latest": dict(
        tier="candidate",
        primary_key=("plan_id", "return_date"),
        watermark_column="return_date",
        rationale="Canonical trailing returns across 14 horizons, dated.",
    ),
    "composition": dict(
        tier="candidate",
        primary_key=("plan_id", "as_on_date"),
        watermark_column="as_on_date",
        rationale=(
            "Actual equity/debt/cash percentages, so hybrids stop contributing "
            "to Equity or Debt purely by their category label."
        ),
    ),
    "fund_stylebox_sebi": dict(
        tier="candidate",
        primary_key=("plan_id", "date"),
        watermark_column="date",
        rationale=(
            "The fund-level valuation table: weighted P/E, P/B, market cap, "
            "style and large/mid/small split. This is the answer to the CFO's "
            "sixth question."
        ),
    ),
    "sip_swp_stp_details": dict(
        tier="candidate",
        primary_key=("plan_id",),
        sync_mode="full",
        rationale=(
            "Scheme-side SIP/STP/SWP rules our mf_sip_mandates must obey. "
            "Overlaps the nested fields on fund_transaction_details."
        ),
    ),
    "schemes_rollingreturns": dict(
        tier="candidate",
        primary_key=("plan_id", "return_date"),
        watermark_column="return_date",
        rationale=(
            "Rolling returns and ranks, 7d to 3650d — the consistency measure "
            "that separates one good year from a durable fund."
        ),
    ),
    "amcs": dict(
        tier="candidate",
        primary_key=("amc_id",),
        sync_mode="full",
        rationale=(
            "FK parent of fund_plans.amc_id. We store the AMC as free text on "
            "mf_fund_metadata.amc_name — no entity, no id, no ownership type."
        ),
    ),
    "colour_code": dict(
        tier="candidate",
        primary_key=("colour_id",),
        sync_mode="full",
        rationale="FK parent of fund_plans.colour — the riskometer band.",
    ),
    "companies": dict(
        tier="candidate",
        primary_key=("company_id",),
        sync_mode="full",
        indexes=(("country_code",), ("industry_code",)),
        rationale=(
            "Issuer master, and one of the two tables that join to countries. "
            "company_metadata on our side is symbol, name and exchange only."
        ),
    ),
    "entity_companies": dict(
        tier="candidate",
        primary_key=("entity_id",),
        sync_mode="full",
        indexes=(("country_code",),),
        rationale=(
            "The link from securities.entity_id to an issuer and its country. "
            "This is the chain that makes the countries master reachable from "
            "fund look-through."
        ),
    ),
    "cat_avg_return_latest_share_class": dict(
        tier="candidate",
        primary_key=("category_id", "return_date", "performance_share_class"),
        watermark_column="return_date",
        rationale=(
            "Peer-group averages. Without it we cannot answer 'does this fund "
            "beat its category', which is the comparison users actually ask."
        ),
    ),
    "fund_rank_latest": dict(
        tier="candidate",
        primary_key=("plan_id", "return_date"),
        watermark_column="return_date",
        rationale="Percentile rank within category per trailing horizon.",
    ),
}


@lru_cache(maxsize=1)
def _catalog() -> dict:
    with CATALOG_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def all_specs() -> dict[str, VrTableSpec]:
    """Every declared table, keyed by VR table name."""
    catalog = _catalog()
    specs: dict[str, VrTableSpec] = {}
    for name, over in _OVERRIDES.items():
        entry = catalog.get(name)
        if entry is None:  # pragma: no cover - guarded by test_specs
            raise KeyError(f"{name!r} has an override but no catalog.json entry")
        columns = tuple(c["name"] for c in entry["columns"])
        missing = [c for c in over["primary_key"] if c not in columns]
        if missing:  # pragma: no cover - guarded by test_specs
            raise ValueError(f"{name}: primary key column(s) {missing} not in catalog")
        for index_cols in over.get("indexes", ()):
            unknown = [c for c in index_cols if c not in columns]
            if unknown:  # pragma: no cover - guarded by test_specs
                raise ValueError(f"{name}: index column(s) {unknown} not in catalog")
        specs[name] = VrTableSpec(
            name=name,
            columns=columns,
            description=entry["description"],
            update_frequency=entry.get("update_frequency"),
            **over,
        )
    return specs


def spec(name: str) -> VrTableSpec:
    try:
        return all_specs()[name]
    except KeyError:
        raise KeyError(f"No VR table spec named {name!r}") from None


def specs_for_tiers(tiers: tuple[Tier, ...]) -> list[VrTableSpec]:
    """Specs in the given tiers, ordered so masters land before the tables
    that reference them and ``deleted_logs`` runs last (it prunes what the
    others just wrote)."""
    order = {"support": 0, "core": 1, "additional": 2, "optional": 3, "candidate": 4}
    chosen = [s for s in all_specs().values() if s.tier in tiers]
    return sorted(
        chosen,
        key=lambda s: (s.name == "deleted_logs", order[s.tier], s.name),
    )
