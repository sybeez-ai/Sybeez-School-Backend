from fastapi import APIRouter, Depends

from src.core.security import verify_service_token
from src.modules.chat.router import router as chat_router
from src.modules.conversations.router import router as conversations_router
from src.modules.evaluation.router import router as evaluation_router
from src.modules.learning.router import router as learning_router
from src.modules.practice.router import router as practice_router

api_router = APIRouter()


@api_router.get("/health")
async def health():
    return {"status": "ok", "service": "sybeez-ai"}


api_router.include_router(chat_router, dependencies=[Depends(verify_service_token)])
api_router.include_router(conversations_router, dependencies=[Depends(verify_service_token)])
api_router.include_router(practice_router, dependencies=[Depends(verify_service_token)])
api_router.include_router(evaluation_router, dependencies=[Depends(verify_service_token)])
api_router.include_router(learning_router, dependencies=[Depends(verify_service_token)])
