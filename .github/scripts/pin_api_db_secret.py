"""Point API and worker task definitions at the school database secret.

A failed deploy rolls the ECS service back to an older task definition. That
revision's DATABASE_URL secret is not the API database. Registering from the
latest family revision, then pinning DATABASE_URL to sybeez-school/api-db,
keeps the rollback from sticking.

Print secret names only. Never print secret values or connection URLs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Resource identifier, not a secret value. The suffix is the Secrets Manager id.
API_DB_SECRET_ARN = (
    "arn:aws:secretsmanager:ap-south-1:213433448375:secret:sybeez-school/api-db-qrTDab"
)
API_DB_SECRET_NAME = "sybeez-school/api-db"
DB_SECRET_KEYS = ("DATABASE_URL", "DATABASE_URL_SYNC")


def friendly_secret_name(value_from: str) -> str:
    marker = ":secret:"
    if marker not in (value_from or ""):
        return "unset"
    name = value_from.split(marker, 1)[1].split(":", 1)[0]
    if len(name) > 7 and name[-7] == "-" and name[-6:].isalnum():
        name = name[:-7]
    return name


def _value_from(key: str) -> str:
    return f"{API_DB_SECRET_ARN}:{key}::"


def report(task: dict) -> None:
    revision = task.get("revision")
    family = task.get("family") or "task"
    print(f"{family} revision={revision}")
    for container in task.get("containerDefinitions") or []:
        name = container.get("name")
        ports = [
            str(item.get("containerPort"))
            for item in container.get("portMappings") or []
            if item.get("containerPort") is not None
        ]
        health = " ".join((container.get("healthCheck") or {}).get("command") or [])
        env_names = [str(item.get("name") or "") for item in container.get("environment") or []]
        command = " ".join(container.get("command") or [])
        print(
            f"{name} ports={','.join(ports) or 'none'} "
            f"health={health or 'none'} command={command or 'image'} "
            f"env={','.join(env_names) or 'none'}"
        )
        for item in container.get("secrets") or []:
            secret_name = str(item.get("name") or "")
            print(f"{name} secret {secret_name} from={friendly_secret_name(str(item.get('valueFrom') or ''))}")


def pin(task: dict) -> bool:
    """Retarget database secrets. Returns True when the document changed."""
    changed = False
    for container in task.get("containerDefinitions") or []:
        name = str(container.get("name") or "")
        environment = []
        for item in container.get("environment") or []:
            key = str(item.get("name") or "")
            if key in DB_SECRET_KEYS:
                print(f"drop plain {key} on {name}")
                changed = True
                continue
            environment.append(item)
        names = {str(item.get("name") or "") for item in environment}
        if "DB_IAM_AUTH" not in names and "DATABASE_IAM_AUTH" not in names:
            environment.append({"name": "DB_IAM_AUTH", "value": "true"})
            print(f"set {name} DB_IAM_AUTH")
            changed = True
        container["environment"] = environment

        secrets = list(container.get("secrets") or [])
        present = {str(item.get("name") or ""): item for item in secrets}
        for key in DB_SECRET_KEYS:
            current = present.get(key)
            wanted = _value_from(key)
            if current is None:
                secrets.append({"name": key, "valueFrom": wanted})
                print(f"pin {name} {key} missing -> {API_DB_SECRET_NAME}")
                changed = True
                continue
            source = friendly_secret_name(str(current.get("valueFrom") or ""))
            if source == API_DB_SECRET_NAME:
                continue
            current["valueFrom"] = wanted
            print(f"pin {name} {key} {source} -> {API_DB_SECRET_NAME}")
            changed = True
        container["secrets"] = secrets

        command = [str(part) for part in container.get("command") or []]
        if command and "alembic" in command:
            container.pop("command", None)
            print(f"cleared alembic command on {name}")
            changed = True
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--report", action="store_true")
    mode.add_argument("--pin", action="store_true")
    args = parser.parse_args(argv)
    task = json.loads(args.path.read_text())
    if args.report:
        report(task)
        return 0
    pin(task)
    args.path.write_text(json.dumps(task))
    report(task)
    return 0


if __name__ == "__main__":
    sys.exit(main())
