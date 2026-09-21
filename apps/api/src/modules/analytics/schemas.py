from pydantic import BaseModel


class AnalyticsStub(BaseModel):
    message: str = "analytics module ready"
