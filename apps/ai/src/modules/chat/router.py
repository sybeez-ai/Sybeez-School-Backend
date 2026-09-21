from fastapi import APIRouter

from src.modules.chat.schemas import ChatRequest, ChatResponse
from src.services.ai_service import AIService

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def chat(body: ChatRequest):
    return await AIService().chat(body)


@router.get("/health")
async def health():
    return {"module": "chat", "status": "ok"}
