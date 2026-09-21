from pydantic import BaseModel


class AchievementsStub(BaseModel):
    message: str = "achievements module ready"
