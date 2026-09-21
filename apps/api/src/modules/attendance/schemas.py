from pydantic import BaseModel


class AttendanceStub(BaseModel):
    message: str = "attendance module ready"
