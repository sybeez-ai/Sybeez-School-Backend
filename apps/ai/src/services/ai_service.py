from pathlib import Path

from src.modules.chat.schemas import ChatRequest, ChatResponse
from src.providers.llm import get_llm
from src.services.context_service import ContextService
from src.services.retrieval_service import RetrievalService


class AIService:
    def __init__(self):
        self.llm = get_llm()
        self.context = ContextService()
        self.retrieval = RetrievalService()

    async def chat(self, req: ChatRequest) -> ChatResponse:
        system = self._load_prompt("system/default.txt")
        teaching_ctx = await self.context.build(req)
        sources = await self.retrieval.search(req.school_id, req.message)
        user_prompt = f"{teaching_ctx}\n\nStudent: {req.message}"
        reply = await self.llm.complete(system, user_prompt)
        return ChatResponse(reply=reply, sources=sources)

    def _load_prompt(self, rel: str) -> str:
        path = Path(__file__).resolve().parents[1] / "prompts" / rel
        return path.read_text() if path.exists() else "You are a helpful AI Teacher."
