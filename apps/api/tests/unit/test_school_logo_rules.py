import pytest

from src.core.exceptions import ConflictError
from src.modules.schools.logo import LOGO_MAX_BYTES, logo_payload


def test_logo_accepts_png_jpg_svg_and_webp():
    assert logo_payload("image/png", b"png")[1] == ".png"
    assert logo_payload("image/jpeg", b"jpg") == ("image/jpeg", ".jpg")
    assert logo_payload("image/webp", b"webp")[1] == ".webp"
    assert logo_payload("image/svg+xml; charset=utf-8", b"<svg></svg>") == ("image/svg+xml", ".svg")


def test_logo_rejects_the_wrong_type_an_empty_file_a_script_and_an_oversize_file():
    with pytest.raises(ConflictError):
        logo_payload("image/gif", b"gif")
    with pytest.raises(ConflictError):
        logo_payload("image/png", b"")
    with pytest.raises(ConflictError):
        logo_payload("image/svg+xml", b"<svg><script>alert(1)</script></svg>")
    with pytest.raises(ConflictError):
        logo_payload("image/png", b"x" * (LOGO_MAX_BYTES + 1))
