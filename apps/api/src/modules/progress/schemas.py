from pydantic import BaseModel


class ProgressStub(BaseModel):
    message: str = "progress module ready"
