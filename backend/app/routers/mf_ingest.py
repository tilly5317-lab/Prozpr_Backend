"""FastAPI router — `mf_ingest.py`.

Declares HTTP routes, dependencies (auth, DB session, user context), and maps request/response schemas. Delegates work to ``app.services`` and returns appropriate status codes and Pydantic models.
"""


from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_effective_user
from app.schemas.ingest import (
    MfAaNormalizeOneResponse,
    MfAaNormalizePendingRequest,
    MfAaNormalizePendingResponse,
)
from app.services.mf_aa_normalizer import (
    get_import_for_user,
    normalize_pending_imports,
    normalize_single_import,
)

router = APIRouter(prefix="/mf-ingest", tags=["MF Ingest"])


@router.post("/normalize/{import_id}", response_model=MfAaNormalizeOneResponse)
async def normalize_import(
    import_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    aa_import = await get_import_for_user(db, current_user.id, import_id)
    if not aa_import:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AA import not found")
    out = await normalize_single_import(db, aa_import)
    return MfAaNormalizeOneResponse(
        import_id=out.import_id,
        status=out.status.value,
        inserted=out.inserted,
        skipped_duplicate=out.skipped_duplicate,
        error=out.error,
    )


@router.post("/normalize-pending", response_model=MfAaNormalizePendingResponse)
async def normalize_pending(
    payload: MfAaNormalizePendingRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    outs = await normalize_pending_imports(db, current_user.id, limit=payload.limit)
    rows = [
        MfAaNormalizeOneResponse(
            import_id=o.import_id,
            status=o.status.value,
            inserted=o.inserted,
            skipped_duplicate=o.skipped_duplicate,
            error=o.error,
        )
        for o in outs
    ]
    return MfAaNormalizePendingResponse(
        total_imports=len(rows),
        total_inserted=sum(r.inserted for r in rows),
        total_skipped_duplicate=sum(r.skipped_duplicate for r in rows),
        results=rows,
    )
