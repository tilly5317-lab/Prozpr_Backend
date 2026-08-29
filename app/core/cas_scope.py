"""CAS snapshot scoping — the read/write rule that replaced the per-upload wipe.

A CAS statement is a *complete* snapshot of a user's mutual-fund holdings, so a
re-upload has to fully replace what the app shows. It used to do that by deleting:
``reset_user_financial_data`` ran 46 DELETEs across 44 tables on every upload, and
every plan, projection and net-worth point ever computed went with it.

Now each upload gets a row in ``cas_uploads`` and everything derived from it is
stamped with that row's id (``CasScoped.cas_upload_id``). Nothing is deleted; the
previous upload is marked ``superseded``, and the app simply stops looking at it.

TWO HOOKS DO ALL THE WORK, so neither reads nor writes have to be hand-edited at
~56 query sites (where one silent miss means double-counted holdings):

  * ``do_orm_execute`` adds ``with_loader_criteria`` to every ORM SELECT that
    touches a :class:`CasScoped` table, restricting it to the active snapshot.
  * ``before_flush`` stamps the active snapshot id onto every new CasScoped row,
    so a rebalancing run, SIP plan, cashflow projection or chat module run
    automatically records which statement it was computed from.

THE NULL RULE: ``cas_upload_id IS NULL`` means "not owned by any statement" and
is ALWAYS visible. That is what keeps manually entered transactions, SimBanks/Finvu
syncs, and not-yet-backfilled legacy rows on screen. It also means the scope can
be switched on safely before the backfill has run — nothing disappears; the worst
case is that legacy rows are counted alongside a new snapshot, which the ingest
closes by adopting orphan rows into a legacy snapshot before it writes a new one
(see ``cas_upload_service.adopt_unscoped_rows``).

WHEN NO SCOPE IS SET the hooks are inert. Request handlers get the scope from
``get_current_user`` / ``get_effective_user``; background jobs and schedulers must
set it themselves with :func:`cas_scope_for_user`, or they will read across every
snapshot the user has.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from contextvars import ContextVar
from weakref import WeakKeyDictionary
from typing import Iterator, Optional

from sqlalchemy import event, or_, select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, with_loader_criteria

from app.core.config import get_settings
from app.domains.ingestion.models.cas_upload import (
    CasScoped,
    CasUpload,
    CasUploadStatus,
)

logger = logging.getLogger(__name__)

# The active snapshot for the current request / task. ``None`` means "no scope" —
# every hook below becomes a no-op, which is the correct behaviour for unauthenticated
# requests, admin tooling and the backfill script.
_ACTIVE_CAS_UPLOAD_ID: ContextVar[Optional[uuid.UUID]] = ContextVar(
    "active_cas_upload_id", default=None
)

_LISTENERS_INSTALLED = False

# Per-engine memo of whether ``cas_uploads`` exists — False during the pre-DDL
# deploy window, or for a test harness that creates only its own tables. Weakly
# keyed so a disposed engine takes its entry with it.
_CAS_UPLOADS_PRESENT: "WeakKeyDictionary[object, bool]" = WeakKeyDictionary()


def versioning_enabled() -> bool:
    """Kill switch. Off → the hooks never engage and the ingest keeps wiping."""
    return get_settings().cas_snapshot_versioning()


# --------------------------------------------------------------------------- scope


def get_scope() -> Optional[uuid.UUID]:
    """The snapshot every scoped read is currently restricted to, if any."""
    return _ACTIVE_CAS_UPLOAD_ID.get()


def set_scope(cas_upload_id: Optional[uuid.UUID]) -> None:
    """Set the scope for the rest of this request/task. Prefer the context managers."""
    _ACTIVE_CAS_UPLOAD_ID.set(cas_upload_id)


@contextlib.contextmanager
def scoped_to(cas_upload_id: Optional[uuid.UUID]) -> Iterator[None]:
    """Run a block against one specific snapshot, restoring the previous scope after.

    The ingest uses this so that everything it reads while rebuilding (holdings
    roll-up, latest-snapshot rebuild, net-worth history) sees the statement it is
    currently importing and nothing from the one it is replacing.
    """
    token = _ACTIVE_CAS_UPLOAD_ID.set(cas_upload_id)
    try:
        yield
    finally:
        _ACTIVE_CAS_UPLOAD_ID.reset(token)


@contextlib.contextmanager
def unscoped() -> Iterator[None]:
    """Run a block across ALL snapshots — history, analytics, backfill, erasure.

    Deliberately explicit: reading every snapshot at once is right for a
    comparison endpoint and wrong for anything the app renders as "current".
    """
    with scoped_to(None):
        yield


async def resolve_active_cas_upload_id(
    db: AsyncSession, user_id: uuid.UUID
) -> Optional[uuid.UUID]:
    """The user's one ``active`` snapshot id, or None if they have never uploaded.

    Cheap: a partial-index lookup on ``cas_uploads (user_id) WHERE status='active'``.
    Runs unscoped — ``cas_uploads`` is not itself a CasScoped table, but a caller
    may already be inside a scope and this must never be filtered by it.

    Degrades to None if the table is not there yet, which covers the deploy window
    between new code and ``apply_postgres_schema_patches`` and test harnesses that
    create only the tables they exercise. No scope is the pre-feature behaviour —
    nothing is hidden, nothing is stamped — so that is the safe direction to fail in.

    The FIRST call against a given engine runs inside a SAVEPOINT. A missing table
    would otherwise abort the caller's whole transaction (Postgres) or force a
    rollback that discards their pending writes; the savepoint keeps the failure
    local to this lookup. The answer is then remembered per engine, so every later
    call is a plain query — per engine and not per process, because a test suite
    runs many databases through one interpreter and one of them having the table
    says nothing about the next.
    """
    stmt = select(CasUpload.id).where(
        CasUpload.user_id == user_id,
        CasUpload.status == CasUploadStatus.ACTIVE.value,
    )
    try:
        bind = db.get_bind()
    except Exception:  # noqa: BLE001 - unbound session: probe every time
        bind = None

    known = _CAS_UPLOADS_PRESENT.get(bind) if bind is not None else None
    if known is False:
        return None
    if known is True:
        return (await db.execute(stmt)).scalars().first()

    try:
        async with db.begin_nested():
            row = (await db.execute(stmt)).scalars().first()
    except (OperationalError, ProgrammingError):
        if bind is not None:
            _CAS_UPLOADS_PRESENT[bind] = False
        logger.warning(
            "cas_uploads is not queryable — CAS snapshot scoping is inert until it is",
            exc_info=True,
        )
        return None
    if bind is not None:
        _CAS_UPLOADS_PRESENT[bind] = True
    return row


async def effective_scope(db: AsyncSession, user_id: uuid.UUID) -> Optional[uuid.UUID]:
    """The scope in force, resolving the user's active snapshot if none is set.

    For code that must be right whether it runs inside a request (scope already
    set) or from a background job (scope unset) — chiefly the rebuild-in-place
    services, whose DELETEs would otherwise reach across snapshots.
    """
    if not versioning_enabled():
        return None
    return get_scope() or await resolve_active_cas_upload_id(db, user_id)


def scope_filter(model, snapshot_id: Optional[uuid.UUID]) -> list:
    """Extra WHERE criteria confining a DELETE/UPDATE to one snapshot.

    Returns [] when versioning is off or no snapshot applies, which leaves the
    statement exactly as it was before this feature — the rebuild-everything
    behaviour the pre-snapshot code relied on.

    Needed because the ``do_orm_execute`` hook only filters SELECTs: a bulk
    DELETE is not a SELECT, so "clear the old rows before writing the new ones"
    would happily clear every previous snapshot's rows too.
    """
    if snapshot_id is None or not versioning_enabled():
        return []
    return [model.cas_upload_id == snapshot_id]


def non_snapshot_filter(model) -> list:
    """Confine a NON-CAS ingest's replace-everything DELETE to its own rows.

    SimBanks and the retired Finvu sync own the rows they write, and those rows
    carry no ``cas_upload_id``. Their "clear then re-sync" DELETEs predate
    snapshots and would otherwise take a user's whole CAS history with them.
    """
    if not versioning_enabled():
        return []
    return [model.cas_upload_id.is_(None)]


@contextlib.asynccontextmanager
async def cas_scope_for_user(db: AsyncSession, user_id: uuid.UUID):
    """Scope a background job to a user's active snapshot.

    Schedulers, the onboarding generation job and any other code that runs
    outside a request have no scope of their own. Without this they read across
    every snapshot the user has ever uploaded, which double-counts holdings.
    """
    if not versioning_enabled():
        yield None
        return
    snapshot_id = await resolve_active_cas_upload_id(db, user_id)
    with scoped_to(snapshot_id):
        yield snapshot_id


# --------------------------------------------------------------------------- hooks


def install_cas_scope_listeners() -> None:
    """Register the SELECT filter and the INSERT stamper. Idempotent."""
    global _LISTENERS_INSTALLED
    if _LISTENERS_INSTALLED:
        return
    _LISTENERS_INSTALLED = True

    @event.listens_for(Session, "do_orm_execute")
    def _scope_orm_selects(
        execute_state,
    ) -> None:  # pragma: no cover - exercised via tests
        """Restrict every ORM SELECT over a CasScoped table to the active snapshot.

        ``is_column_load`` / ``is_relationship_load`` are excluded per SQLAlchemy's
        documented recipe: those are refreshes of rows already loaded, and filtering
        them would raise on a row that no longer matches.
        """
        if (
            not execute_state.is_select
            or execute_state.is_column_load
            or execute_state.is_relationship_load
        ):
            return
        snapshot_id = _ACTIVE_CAS_UPLOAD_ID.get()
        if snapshot_id is None or not versioning_enabled():
            return
        execute_state.statement = execute_state.statement.options(
            with_loader_criteria(
                CasScoped,
                # NULL is always visible — see THE NULL RULE in the module docstring.
                lambda cls: or_(
                    cls.cas_upload_id == snapshot_id,
                    cls.cas_upload_id.is_(None),
                ),
                include_aliases=True,
            )
        )

    @event.listens_for(Session, "before_flush")
    def _stamp_new_rows(session: Session, flush_context, instances) -> None:  # noqa: ARG001
        """Stamp the active snapshot onto every new CasScoped row.

        This is what makes "which statement was this plan computed from?" true for
        rebalancing runs, SIP/lumpsum plans, cashflow projections and chat module
        runs without each of those services having to know the feature exists.

        Only ``session.new`` and only when unset: an explicit id already assigned by
        the ingest always wins, and existing rows are never re-stamped (a superseded
        snapshot's rows must keep pointing at the snapshot that produced them).
        """
        snapshot_id = _ACTIVE_CAS_UPLOAD_ID.get()
        if snapshot_id is None or not versioning_enabled():
            return
        for obj in session.new:
            if (
                isinstance(obj, CasScoped)
                and getattr(obj, "cas_upload_id", None) is None
            ):
                obj.cas_upload_id = snapshot_id

    logger.info("CAS snapshot scope listeners installed")
