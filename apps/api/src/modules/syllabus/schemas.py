from pydantic import BaseModel


class SyllabusStub(BaseModel):
    message: str = "syllabus module ready"
