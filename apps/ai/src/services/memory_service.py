from uuid import UUID


class MemoryService:
    async def recall(self, school_id: UUID, student_id: UUID, query: str) -> list[str]:
        return []
