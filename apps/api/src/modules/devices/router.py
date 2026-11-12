"""Mobile push token registration (Sybeez School app)."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, get_current_user
from src.core.database import get_db
from src.db.models import PushDevice

router = APIRouter(prefix="/devices", tags=["devices"])


class PushTokenIn(BaseModel):
    token: str = Field(min_length=8, max_length=255)
    platform: Literal["ios", "android", "web"]
    provider: Literal["expo", "fcm", "apns"] = "expo"


class PushTokenDelete(BaseModel):
    token: str = Field(min_length=8, max_length=255)


class PushTokenOut(BaseModel):
    id: str
    registered: bool = True


@router.post("/push-token", response_model=PushTokenOut)
async def register_push_token(
    body: PushTokenIn,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Register (or move) this device's push token to the signed-in user. Idempotent."""
    rid = (
        await db.execute(
            text("SELECT app.claim_push_token(:t, :p, :v, :s)"),
            {"t": body.token, "p": body.platform, "v": body.provider, "s": user.school_id},
        )
    ).scalar_one()
    await db.commit()
    return PushTokenOut(id=str(rid))


@router.delete("/push-token", status_code=204)
async def unregister_push_token(
    body: PushTokenDelete,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Called on sign-out so the previous user stops receiving pushes on this phone."""
    await db.execute(delete(PushDevice).where(PushDevice.token == body.token, PushDevice.user_id == user.id))
    await db.commit()
