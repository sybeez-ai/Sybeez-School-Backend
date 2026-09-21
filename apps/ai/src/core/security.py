from fastapi import Header, HTTPException, status

from src.core.config import get_settings


async def verify_service_token(x_ai_token: str | None = Header(default=None, alias="X-AI-Token")) -> None:
    if x_ai_token != get_settings().ai_service_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid AI service token")
