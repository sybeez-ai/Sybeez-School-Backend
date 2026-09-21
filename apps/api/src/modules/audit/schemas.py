from pydantic import BaseModel


class AuditStub(BaseModel):
    message: str = "audit module ready"
