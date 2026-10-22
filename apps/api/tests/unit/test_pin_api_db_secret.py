"""Rolled-back task definitions must not keep the wrong database secret."""

import importlib.util
import json
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[4] / ".github" / "scripts" / "pin_api_db_secret.py"
_spec = importlib.util.spec_from_file_location("pin_api_db_secret", _SCRIPT)
pin_mod = importlib.util.module_from_spec(_spec)
assert _spec is not None and _spec.loader is not None
_spec.loader.exec_module(pin_mod)


def test_friendly_name_strips_the_secrets_manager_suffix():
    value = (
        "arn:aws:secretsmanager:ap-south-1:213433448375:secret:"
        "sybeez-school/ai-A18n9V:DATABASE_URL::"
    )
    assert pin_mod.friendly_secret_name(value) == "sybeez-school/ai"


def test_pin_replaces_a_rolled_back_database_secret_without_keeping_the_url():
    task = {
        "family": "sybeez-school-api",
        "revision": 2,
        "containerDefinitions": [
            {
                "name": "api",
                "command": ["alembic", "upgrade", "head"],
                "environment": [
                    {"name": "APP_ENV", "value": "production"},
                    {"name": "DATABASE_URL", "value": "postgresql+asyncpg://user:secret@db/app"},
                ],
                "secrets": [
                    {
                        "name": "DATABASE_URL",
                        "valueFrom": (
                            "arn:aws:secretsmanager:ap-south-1:213433448375:secret:"
                            "sybeez-school/ai-A18n9V:DATABASE_URL::"
                        ),
                    }
                ],
                "portMappings": [{"containerPort": 8000}],
            }
        ],
    }
    assert pin_mod.pin(task) is True
    container = task["containerDefinitions"][0]
    assert "command" not in container
    assert [item["name"] for item in container["environment"]] == ["APP_ENV", "DB_IAM_AUTH"]
    assert container["environment"][1]["value"] == "true"
    secrets = {item["name"]: item["valueFrom"] for item in container["secrets"]}
    assert secrets["DATABASE_URL"].endswith(":DATABASE_URL::")
    assert "sybeez-school/api-db-" in secrets["DATABASE_URL"]
    assert "sybeez-school/ai-" not in secrets["DATABASE_URL"]
    assert "secret@" not in json.dumps(secrets)
    assert pin_mod.friendly_secret_name(secrets["DATABASE_URL"]) == "sybeez-school/api-db"
    assert pin_mod.pin(task) is False
