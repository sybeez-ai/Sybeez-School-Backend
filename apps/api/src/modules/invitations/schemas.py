from pydantic import BaseModel


class InvitationsStub(BaseModel):
    message: str = "invitations module ready"
