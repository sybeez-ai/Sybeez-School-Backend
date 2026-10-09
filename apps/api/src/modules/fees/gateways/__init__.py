from src.modules.fees.gateways.base import PaymentGatewayAdapter
from src.modules.fees.gateways.mock import MockGateway
from src.modules.fees.gateways.razorpay import RazorpayGateway


def get_gateway(provider: str | None = None) -> PaymentGatewayAdapter:
    name = (provider or "mock").lower()
    if name == "razorpay":
        return RazorpayGateway()
    return MockGateway()
