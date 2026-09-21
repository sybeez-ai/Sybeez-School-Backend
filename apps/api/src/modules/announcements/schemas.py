from pydantic import BaseModel


class AnnouncementsStub(BaseModel):
    message: str = "announcements module ready"
