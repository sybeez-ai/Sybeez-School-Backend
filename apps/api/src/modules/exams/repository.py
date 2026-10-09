# Repository hooks kept for module layout consistency; ExamService uses the session directly.

from sqlalchemy.ext.asyncio import AsyncSession


class ExamsRepository:
    def __init__(self, db: AsyncSession):
        self.db = db
