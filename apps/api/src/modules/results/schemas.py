from pydantic import BaseModel


class ResultsStub(BaseModel):
    message: str = "results module ready"
