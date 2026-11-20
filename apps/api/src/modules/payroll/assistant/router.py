"""Streaming payroll assistant. Writes wait for a confirmed, idempotent request."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, get_current_user
from src.core.database import get_db
from src.modules.payroll.assistant.downloads import read_download
from src.modules.payroll.assistant.imports import PayrollSheetImport
from src.modules.payroll.assistant.service import PayrollAssistant
from src.modules.payroll.ratelimit import assistant_rate_limit, payroll_rate_limit
from src.modules.payroll.service import PayrollService
from src.modules.access.deps import public

router = APIRouter(prefix="/payroll/assistant", tags=["payroll-assistant"])
Db = Annotated[AsyncSession, Depends(get_db)]


class AssistantMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class AssistantChatIn(BaseModel):
    messages: list[AssistantMessage] = Field(min_length=1, max_length=20)


class AssistantConfirmIn(BaseModel):
    proposal_id: UUID
    idempotency_key: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")


class ImportConfirmIn(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")


async def assistant_user(
    request: Request,
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> CurrentUser:
    await payroll_rate_limit(request, user)
    await assistant_rate_limit(request, user)
    return user


User = Annotated[CurrentUser, Depends(assistant_user)]


@router.post(
    "/chat",
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
async def chat(body: AssistantChatIn, user: User, db: Db):
    assistant = PayrollAssistant(PayrollService(db, user))

    async def events():
        async for piece in assistant.stream([item.model_dump() for item in body.messages]):
            yield piece

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/confirm")
async def confirm(body: AssistantConfirmIn, user: User, db: Db):
    return await PayrollAssistant(PayrollService(db, user)).confirm(body.proposal_id, body.idempotency_key)


@router.post("/import")
async def import_preview(
    user: User,
    db: Db,
    file: UploadFile = File(...),
    financial_year: str | None = Form(default=None),
    effective_from: date | None = Form(default=None),
):
    content = await file.read()
    return await PayrollSheetImport(PayrollService(db, user)).preview(
        filename=file.filename or "",
        content=content,
        content_type=file.content_type,
        financial_year=financial_year,
        effective_from=effective_from,
    )


@router.post("/import/{import_id}/confirm")
async def import_confirm(import_id: UUID, body: ImportConfirmIn, user: User, db: Db):
    return await PayrollSheetImport(PayrollService(db, user)).confirm(import_id, body.idempotency_key)


@router.get("/import/{import_id}/rejects.csv")
async def import_rejects(import_id: UUID, user: User, db: Db):
    body = await PayrollSheetImport(PayrollService(db, user)).rejects_csv(import_id)
    return Response(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="import-rejects.csv"'},
    )


@router.get("/downloads/{token}")
@public
async def download(token: str):
    data, content_type, filename = read_download(token)
    safe = filename.replace('"', "")
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{safe}"', "Cache-Control": "no-store"},
    )
