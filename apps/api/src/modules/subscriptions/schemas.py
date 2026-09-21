from pydantic import BaseModel


class SubscriptionsStub(BaseModel):
    message: str = "subscriptions module ready"
