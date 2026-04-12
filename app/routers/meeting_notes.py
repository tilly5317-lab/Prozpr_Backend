"""FastAPI router — `meeting_notes.py`.

Declares HTTP routes, dependencies (auth, DB session, user context), and maps request/response schemas. Delegates work to ``app.services`` and returns appropriate status codes and Pydantic models.
"""


from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import CurrentUser, get_effective_user
from app.models.meeting_note import MeetingNote, MeetingNoteItem, MeetingNoteItemType
from app.schemas.meeting_note import (
    MeetingNoteCreate,
    MeetingNoteDetailResponse,
    MeetingNoteItemCreate,
    MeetingNoteItemResponse,
    MeetingNoteItemUpdate,
    MeetingNoteResponse,
    MeetingNoteUpdate,
)

router = APIRouter(prefix="/meeting-notes", tags=["Meeting Notes"])


@router.get("/", response_model=list[MeetingNoteResponse])
async def list_meeting_notes(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = (
        select(MeetingNote)
        .where(MeetingNote.user_id == current_user.id)
        .order_by(MeetingNote.created_at.desc())
    )
    result = await db.execute(stmt)
    return [MeetingNoteResponse.model_validate(n) for n in result.scalars().all()]


@router.post("/", response_model=MeetingNoteResponse, status_code=status.HTTP_201_CREATED)
async def create_meeting_note(
    payload: MeetingNoteCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    note = MeetingNote(
        user_id=current_user.id,
        title=payload.title,
        meeting_date=payload.meeting_date,
    )
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return MeetingNoteResponse.model_validate(note)


@router.get("/{note_id}", response_model=MeetingNoteDetailResponse)
async def get_meeting_note(
    note_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = (
        select(MeetingNote)
        .options(selectinload(MeetingNote.items))
        .where(MeetingNote.id == note_id, MeetingNote.user_id == current_user.id)
    )
    note = (await db.execute(stmt)).scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting note not found")

    return MeetingNoteDetailResponse(
        **MeetingNoteResponse.model_validate(note).model_dump(),
        items=[MeetingNoteItemResponse.model_validate(i) for i in note.items],
    )


@router.put("/{note_id}", response_model=MeetingNoteResponse)
async def update_meeting_note(
    note_id: uuid.UUID,
    payload: MeetingNoteUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(MeetingNote).where(
        MeetingNote.id == note_id, MeetingNote.user_id == current_user.id
    )
    note = (await db.execute(stmt)).scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting note not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(note, field, value)

    await db.commit()
    await db.refresh(note)
    return MeetingNoteResponse.model_validate(note)


@router.post("/{note_id}/approve-mandate")
async def approve_mandate(
    note_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(MeetingNote).where(
        MeetingNote.id == note_id, MeetingNote.user_id == current_user.id
    )
    note = (await db.execute(stmt)).scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting note not found")

    note.is_mandate_approved = True
    await db.commit()
    return {"message": "Mandate approved", "meeting_note_id": str(note_id)}


@router.post("/{note_id}/items", response_model=MeetingNoteItemResponse, status_code=status.HTTP_201_CREATED)
async def add_item(
    note_id: uuid.UUID,
    payload: MeetingNoteItemCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(MeetingNote).where(
        MeetingNote.id == note_id, MeetingNote.user_id == current_user.id
    )
    if not (await db.execute(stmt)).scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting note not found")

    item = MeetingNoteItem(
        meeting_note_id=note_id,
        item_type=MeetingNoteItemType(payload.item_type),
        role=payload.role,
        content=payload.content,
        sort_order=payload.sort_order,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return MeetingNoteItemResponse.model_validate(item)


@router.put("/{note_id}/items/{item_id}", response_model=MeetingNoteItemResponse)
async def update_item(
    note_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: MeetingNoteItemUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(MeetingNote).where(
        MeetingNote.id == note_id, MeetingNote.user_id == current_user.id
    )
    if not (await db.execute(stmt)).scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting note not found")

    item_stmt = select(MeetingNoteItem).where(
        MeetingNoteItem.id == item_id, MeetingNoteItem.meeting_note_id == note_id
    )
    item = (await db.execute(item_stmt)).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, value)

    await db.commit()
    await db.refresh(item)
    return MeetingNoteItemResponse.model_validate(item)
