"""GST on a vendor bill rounds half-up to paise."""

from decimal import Decimal

from src.modules.finance.expenses import _gst


def test_gst_rounds_half_up_to_paise():
    gst_amount, total = _gst(Decimal("10.03"), Decimal("18"))
    assert gst_amount == Decimal("1.81")
    assert total == Decimal("11.84")

    even, total_even = _gst(Decimal("1000"), Decimal("18"))
    assert even == Decimal("180.00")
    assert total_even == Decimal("1180.00")
