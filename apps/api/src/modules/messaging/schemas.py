from pydantic import BaseModel


class MessagingStub(BaseModel):
    message: str = "messaging module ready"
