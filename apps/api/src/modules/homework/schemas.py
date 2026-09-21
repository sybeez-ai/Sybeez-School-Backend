from pydantic import BaseModel


class HomeworkStub(BaseModel):
    message: str = "homework module ready"
