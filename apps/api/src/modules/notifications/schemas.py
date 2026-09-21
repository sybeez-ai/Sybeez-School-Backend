from pydantic import BaseModel


class NotificationsStub(BaseModel):
    message: str = "notifications module ready"
