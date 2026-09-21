from uuid import UUID


class RetrievalService:
    async def search(self, school_id: UUID, query: str, k: int = 5) -> list[str]:
        # RAG over knowledge_chunks (pgvector) — wired via Core API / worker later
        return []
