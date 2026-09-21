from pydantic import BaseModel


class UsersStub(BaseModel):
    message: str = "users module ready"
