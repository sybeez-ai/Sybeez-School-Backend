from pydantic import BaseModel


class AssignmentsStub(BaseModel):
    message: str = "assignments module ready"
