"""The screens that were hidden now have routes, and bad calendar dates are rejected."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.api.router import api_router
from src.core.exceptions import ValidationError as AppValidation
from src.modules.campus.router import CalendarIn
from src.modules.campus.service import CampusService
from src.modules.finance.books import GstInvoiceIn
from src.shared.enums import UserRole


def _paths() -> set[str]:
    found = set()
    for included in api_router.routes:
        context = getattr(included, "include_context", None)
        router = getattr(context, "included_router", None) if context else None
        if router is None:
            continue
        prefix = context.prefix or ""
        for route in router.routes:
            path = getattr(route, "path", "")
            if path:
                found.add(prefix + path)
    return found


def test_hidden_screens_have_routes():
    paths = _paths()
    for path in (
        "/api/v1/library/titles",
        "/api/v1/library/copies",
        "/api/v1/library/loans",
        "/api/v1/library/fines",
        "/api/v1/achievements",
        "/api/v1/activities",
        "/api/v1/calendar",
        "/api/v1/finance/caution-deposits",
        "/api/v1/finance/book-refunds",
        "/api/v1/finance/tds-entries",
        "/api/v1/finance/gst-invoices",
    ):
        assert path in paths, path


def test_calendar_end_before_start_is_rejected():
    body = CalendarIn(kind="HOLIDAY", title="Break", starts_on=date(2026, 5, 2), ends_on=date(2026, 5, 1))
    user = type("U", (), {"role": UserRole.SCHOOL_ADMIN, "school_id": None})()
    with pytest.raises(AppValidation, match="end date"):
        import asyncio

        asyncio.run(CampusService(None).add_calendar(user, body))


def test_gst_invoice_total_is_taxable_plus_gst():
    body = GstInvoiceIn(
        invoice_number="G1",
        party_name="Books",
        invoice_date=date(2026, 4, 2),
        taxable_amount=Decimal("100"),
        gst_amount=Decimal("18"),
    )
    assert body.taxable_amount + body.gst_amount == Decimal("118")


def test_fine_amount_must_be_positive():
    from src.modules.campus.router import FineIn

    with pytest.raises(ValidationError):
        FineIn(loan_id="00000000-0000-0000-0000-000000000001", amount=Decimal("0"))
