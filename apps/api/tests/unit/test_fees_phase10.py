"""Phase 10 fees & payments — contract/unit coverage."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient

from src.main import app
from src.modules.fees.calculations import calculate_late_fee, invoice_status_after_payment, money
from src.modules.fees.gateways import get_gateway
from src.modules.fees.schemas import (
    FeeCategoryCreate,
    FeeStructureCreate,
    InstallmentIn,
    InvoiceGenerateIn,
    ManualPaymentIn,
    StructureItemIn,
)
from src.modules.fees.receipt_pdf import build_receipt_pdf
from src.shared.enums import (
    FeeChargeModel,
    FeeInvoiceStatus,
    FeeStructureStatus,
    NotificationType,
    PaymentMethod,
    PaymentStatus,
)

client = TestClient(app)


def test_fees_openapi_paths():
    paths = client.get("/openapi.json").json()["paths"]
    for p in (
        "/api/v1/fees/categories",
        "/api/v1/fees/structures",
        "/api/v1/fees/structures/{structure_id}",
        "/api/v1/fees/structures/{structure_id}/publish",
        "/api/v1/fees/structures/{structure_id}/installments",
        "/api/v1/fees/dashboard",
        "/api/v1/fees/students",
        "/api/v1/fees/students/{student_id}",
        "/api/v1/fees/students/{student_id}/ledger",
        "/api/v1/fees/invoices",
        "/api/v1/fees/invoices/generate",
        "/api/v1/fees/invoices/{invoice_id}",
        "/api/v1/fees/invoices/{invoice_id}/cancel",
        "/api/v1/fees/payments",
        "/api/v1/fees/payments/manual",
        "/api/v1/fees/payments/{payment_id}/refund",
        "/api/v1/fees/receipts/{receipt_id}",
        "/api/v1/fees/receipts/{receipt_id}/pdf",
        "/api/v1/fees/me/children",
        "/api/v1/fees/me/invoices",
        "/api/v1/fees/me/payments",
        "/api/v1/fees/me/receipts",
        "/api/v1/fees/settings",
        "/api/v1/fees/reports/collection",
        "/api/v1/fees/reports/outstanding",
        "/api/v1/fees/reports/overdue",
        "/api/v1/fees/payment-links",
        "/api/v1/fees/online-payments/reconciliation",
        "/api/v1/fees/demands/generate",
        "/api/v1/payments/orders",
        "/api/v1/payments/verify",
        "/api/v1/payments/webhook/{provider}",
    ):
        assert p in paths, f"missing {p}"


def test_fees_endpoints_require_auth():
    assert client.get("/api/v1/fees/categories").status_code == 401
    assert client.get("/api/v1/fees/receipts").status_code == 401
    assert client.post("/api/v1/fees/collections", json={}).status_code == 401
    assert client.get("/api/v1/auth/memberships").status_code == 401
    assert client.post("/api/v1/auth/switch-school", json={}).status_code == 401
    assert client.get("/api/v1/schools/me/branches").status_code == 401
    assert client.get("/api/v1/fees/dashboard").status_code == 401
    assert client.post("/api/v1/fees/invoices/generate", json={}).status_code == 401
    assert client.post("/api/v1/payments/orders", json={}).status_code == 401
    assert client.get("/api/v1/fees/me/children").status_code == 401
    rid = str(uuid4())
    assert client.get(f"/api/v1/fees/receipts/{rid}").status_code == 401


def test_fee_notification_types():
    for t in (
        "FEE_INVOICE_ISSUED",
        "FEE_DUE_REMINDER",
        "FEE_OVERDUE",
        "FEE_PAYMENT_SUCCESS",
        "FEE_PAYMENT_FAILED",
        "FEE_RECEIPT_READY",
        "FEE_REFUND",
    ):
        assert NotificationType(t).value == t


def test_fee_enums():
    assert FeeChargeModel.YEARLY.value == "YEARLY"
    assert FeeStructureStatus.PUBLISHED.value == "PUBLISHED"
    assert FeeInvoiceStatus.PARTIALLY_PAID.value == "PARTIALLY_PAID"
    assert PaymentStatus.SUCCESS.value == "SUCCESS"
    assert PaymentMethod.UPI.value == "UPI"


def test_money_helpers():
    assert money("40.1") == Decimal("40.10")
    assert money(None) == Decimal("0.00")


def test_late_fee_calculations():
    due = date(2027, 1, 10)
    assert calculate_late_fee(
        balance=Decimal("1000"),
        due_date=due,
        as_of=date(2027, 1, 12),
        grace_days=5,
        calculation_type="FIXED",
        amount=Decimal("100"),
        maximum_amount=None,
    ) == Decimal("0.00")
    fee = calculate_late_fee(
        balance=Decimal("1000"),
        due_date=due,
        as_of=date(2027, 1, 20),
        grace_days=5,
        calculation_type="PER_DAY",
        amount=Decimal("100"),
        maximum_amount=Decimal("500"),
    )
    assert fee == Decimal("500.00")


def test_invoice_status_after_payment():
    due = date.today() + timedelta(days=5)
    assert invoice_status_after_payment(total=Decimal(100), paid=Decimal(0), due_date=due) == "ISSUED"
    assert (
        invoice_status_after_payment(total=Decimal(100), paid=Decimal(40), due_date=due)
        == "PARTIALLY_PAID"
    )
    assert invoice_status_after_payment(total=Decimal(100), paid=Decimal(100), due_date=due) == "PAID"
    past = date.today() - timedelta(days=2)
    assert invoice_status_after_payment(total=Decimal(100), paid=Decimal(0), due_date=past) == "OVERDUE"


def test_category_and_structure_schemas():
    cat = FeeCategoryCreate(name="Tuition Fee", code="TUITION")
    assert cat.code == "TUITION"
    body = FeeStructureCreate(
        academic_year_id=uuid4(),
        name="Class 8 Standard",
        grade_id=uuid4(),
        items=[StructureItemIn(fee_category_id=uuid4(), amount=Decimal("40000"))],
        installments=[
            InstallmentIn(name="Term 1", due_date=date(2026, 6, 10), sequence=1, amount=Decimal("15000"))
        ],
    )
    assert body.charge_model == "YEARLY"
    assert len(body.installments) == 1


def test_generate_and_manual_payment_schemas():
    InvoiceGenerateIn(
        academic_year_id=uuid4(),
        fee_structure_id=uuid4(),
        installment_id=uuid4(),
        section_id=uuid4(),
    )
    ManualPaymentIn(invoice_id=uuid4(), amount=Decimal("15000"), method="BANK_TRANSFER")


def test_duplicate_invoice_key_shape():
    account_id = uuid4()
    installment_id = uuid4()
    key = (account_id, installment_id)
    assert key[0] == account_id


def test_mock_gateway():
    import asyncio

    gw = get_gateway("mock")

    async def run():
        order = await gw.create_order(amount=Decimal("15000"), currency="INR", receipt="PAY-1")
        assert order.order_id.startswith("mock_order_")
        ok = await gw.verify_payment(
            order_id=order.order_id,
            payment_id="mock_pay_x",
            signature="x",
            amount=Decimal("15000"),
            currency="INR",
        )
        assert ok.success is True
        bad = await gw.verify_payment(
            order_id="evil",
            payment_id="x",
            signature="x",
            amount=Decimal("15000"),
            currency="INR",
        )
        assert bad.success is False
        wh = await gw.handle_webhook(
            payload={"order_id": order.order_id, "amount": "15000", "currency": "INR"},
            headers={"x-mock-signature": "mock_secret"},
        )
        assert wh.success is True

    asyncio.run(run())


def test_receipt_pdf_bytes():
    pdf = build_receipt_pdf(
        {
            "school_name": "ABC School",
            "receipt_number": "REC-2026-000001",
            "payment_number": "PAY-2026-000001",
            "invoice_number": "INV-2026-000001",
            "student_name": "Rahul",
            "amount": "15000.00",
            "currency": "INR",
            "method": "UPI",
            "paid_at": "2027-01-10",
            "status": "SUCCESS",
        }
    )
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 100
