from pydantic import BaseModel


class ReportsStub(BaseModel):
    message: str = "reports module ready"
