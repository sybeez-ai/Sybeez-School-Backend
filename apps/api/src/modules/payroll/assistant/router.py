"""Streaming payroll assistant. Writes wait for a confirmed, idempotent request."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, get_current_user
from src.core.database import get_db
from src.modules.payroll.assistant.service import PayrollAssistant
from src.modules.payroll.ratelimit import assistant_rate_limit, payroll_rate_limit
from src.modules.payroll.service import PayrollService

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
