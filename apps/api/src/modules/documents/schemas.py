from pydantic import BaseModel


class DocumentsStub(BaseModel):
    message: str = "documents module ready"
