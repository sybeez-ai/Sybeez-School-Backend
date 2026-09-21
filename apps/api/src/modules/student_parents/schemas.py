from pydantic import BaseModel


class StudentParentsStub(BaseModel):
    message: str = "student_parents module ready"
