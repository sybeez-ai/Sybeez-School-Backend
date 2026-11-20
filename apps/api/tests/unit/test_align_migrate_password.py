"""Pure helpers for the migrate password alignment. No AWS calls and no secret values."""

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]
_SCRIPT = _ROOT / ".github" / "scripts" / "align_migrate_master_password.py"
_spec = importlib.util.spec_from_file_location("align_migrate_master_password", _SCRIPT)
assert _spec and _spec.loader
align = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(align)


def test_parse_value_from_splits_the_json_key():
    secret_id, key = align.parse_value_from(
        "arn:aws:secretsmanager:ap-south-1:213433448375:secret:sybeez-school/migrate-AbCd12:DATABASE_URL::"
    )
    assert secret_id.endswith("migrate-AbCd12")
    assert key == "DATABASE_URL"


def test_replace_password_keeps_the_rest_of_the_url():
    updated = align.replace_password(
        "postgresql://cyberschool_admin:old@sybeez-school-db.example:5432/sybeez?sslmode=require",
        "new pass",
    )
    assert updated.startswith("postgresql://cyberschool_admin:new%20pass@sybeez-school-db.example:5432/sybeez")
    assert updated.endswith("sslmode=require")
    assert align.password_of(updated) == "new pass"


def test_master_password_reads_the_rds_json_shape():
    assert align.master_password('{"username":"cyberschool_admin","password":"from-rds"}') == "from-rds"
    assert align.master_password("not-a-secret-shape") is None


def test_secret_with_password_updates_only_the_url_password():
    updated = align.secret_with_password(
        '{"DATABASE_URL":"postgresql://cyberschool_admin:old@db.example:5432/sybeez"}',
        "DATABASE_URL",
        "from-rds",
    )
    assert "from-rds" in updated
    assert "old" not in updated
