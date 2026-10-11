"""Legacy finance revision ids must not be confused with HRM."""

from src.db.migrations.finance_stamp import STAMP_TARGET, assert_master_url, classify


def test_other_revisions_are_left_alone():
    assert classify("0027", hr=False, finance=False) == "skip"
    assert classify("0043", hr=True, finance=True) == "skip"
    assert classify(None, hr=False, finance=False) == "skip"


def test_hrm_chain_is_not_restamped():
    assert classify("0028", hr=True, finance=False) == "hrm"
    assert classify("0029", hr=True, finance=False) == "hrm"
    assert classify("0030", hr=True, finance=False) == "hrm"


def test_legacy_finance_chain_restamps_to_shared_parent():
    assert classify("0028", hr=False, finance=True) == "finance"
    assert classify("0029", hr=False, finance=True) == "finance"
    assert classify("0030", hr=False, finance=True) == "finance"
    assert STAMP_TARGET == "0027"


def test_mixed_or_unknown_stamp_is_ambiguous():
    assert classify("0030", hr=True, finance=True) == "ambiguous"
    assert classify("0030", hr=False, finance=False) == "ambiguous"
    assert classify("0028", hr=False, finance=False) == "ambiguous"


def test_migrate_refuses_iam_without_a_master_password():
    try:
        assert_master_url("postgresql+asyncpg://sybeez_app@db.example:5432/sybeez", iam=True)
    except RuntimeError as exc:
        assert "master" in str(exc).lower()
    else:
        raise AssertionError("expected RuntimeError")


def test_migrate_keeps_master_url_when_password_is_present():
    assert_master_url(
        "postgresql+asyncpg://master:secret@db.example:5432/sybeez",
        iam=True,
    )
    assert_master_url("postgresql+asyncpg://sybeez:sybeez@localhost/sybeez", iam=False)
