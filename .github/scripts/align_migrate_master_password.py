"""Make the migrate task's master password match RDS.

The migrate task signs in as the RDS master user with the password inside its
DATABASE_URL secret. The API uses a different secret and IAM auth.

When RDS has a managed master secret, that secret is the password RDS will
accept, so the migrate secret's URL is updated to it. Otherwise RDS is set to
the password already stored for the migrate task.

Secret values are masked and never printed. Nothing is deleted.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from urllib.parse import quote, unquote, urlparse, urlunparse

DB_INSTANCE = "sybeez-school-db"
TASK_FAMILY = "sybeez-school-migrate"
SECRET_ENV = "DATABASE_URL"
VALUE_FROM_RE = re.compile(
    r"^(arn:aws:secretsmanager:[^:]+:[^:]+:secret:.+-[A-Za-z0-9]{6})(?::([^:]*):([^:]*):([^:]*))?$"
)


def mask(value: str) -> None:
    """Hide a secret in the Actions log. The value is not written anywhere else."""
    if not value:
        return
    escaped = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::add-mask::{escaped}", flush=True)


def redact(text: str, secrets: list[str]) -> str:
    cleaned = text
    for secret in secrets:
        if secret:
            cleaned = cleaned.replace(secret, "***")
    return cleaned


def parse_value_from(value_from: str) -> tuple[str, str | None]:
    """Return the secret id and optional JSON key from an ECS valueFrom."""
    match = VALUE_FROM_RE.match(value_from or "")
    if not match:
        raise SystemExit(f"DATABASE_URL valueFrom is not a Secrets Manager ARN ({friendly_name(value_from)})")
    secret_id, key, _stage, _version = match.groups()
    return secret_id, key or None


def friendly_name(value_from: str) -> str:
    marker = ":secret:"
    if marker not in (value_from or ""):
        return "unset"
    name = value_from.split(marker, 1)[1].split(":", 1)[0]
    if len(name) > 7 and name[-7] == "-" and name[-6:].isalnum():
        name = name[:-7]
    return name


def replace_password(url: str, password: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.hostname or parsed.username is None:
        raise SystemExit("DATABASE_URL is not a database URL with a user")
    port = f":{parsed.port}" if parsed.port else ""
    userinfo = f"{quote(unquote(parsed.username), safe='')}:{quote(password, safe='')}"
    return urlunparse(
        (parsed.scheme, f"{userinfo}@{parsed.hostname}{port}", parsed.path, parsed.params, parsed.query, parsed.fragment)
    )


def password_of(url: str) -> str:
    password = urlparse(url).password
    if not password:
        raise SystemExit("DATABASE_URL has no password")
    return unquote(password)


def url_from_secret(secret_string: str, json_key: str | None) -> str:
    if json_key:
        try:
            payload = json.loads(secret_string)
        except json.JSONDecodeError as exc:
            raise SystemExit("DATABASE_URL secret is not JSON") from exc
        url = payload.get(json_key)
        if not isinstance(url, str):
            raise SystemExit("DATABASE_URL secret is missing that key")
        return url
    return secret_string.strip()


def secret_with_password(secret_string: str, json_key: str | None, password: str) -> str:
    if json_key:
        payload = json.loads(secret_string)
        payload[json_key] = replace_password(str(payload[json_key]), password)
        return json.dumps(payload)
    return replace_password(secret_string.strip(), password)


def master_password(secret_string: str) -> str | None:
    """Password from an RDS-managed secret, or None when it is not one."""
    stripped = secret_string.strip()
    if stripped.startswith("postgres://") or stripped.startswith("postgresql://"):
        return password_of(stripped)
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    password = payload.get("password") if isinstance(payload, dict) else None
    return password if isinstance(password, str) and password else None


class Aws:
    def __init__(self) -> None:
        self.secrets: list[str] = []

    def remember(self, value: str) -> None:
        if value and value not in self.secrets:
            self.secrets.append(value)
            mask(value)

    def run(self, args: list[str], *, operation: str) -> str:
        proc = subprocess.run(["aws", *args], capture_output=True, text=True)
        if proc.returncode != 0:
            message = redact(proc.stderr.strip() or proc.stdout.strip(), self.secrets)
            denied = "accessdenied" in message.lower() or "not authorized" in message.lower()
            print(f"AWS role lacks permission for {operation}" if denied else f"{operation} failed")
            print(message)
            raise SystemExit(3 if denied else 1)
        return proc.stdout

    def json(self, args: list[str], *, operation: str) -> dict:
        return json.loads(self.run(args, operation=operation) or "{}")


def find_database_url(task: dict) -> tuple[str, str | None, str]:
    """Return valueFrom, json key, and where it was found."""
    for container in task.get("containerDefinitions") or []:
        name = str(container.get("name") or "")
        for item in container.get("secrets") or []:
            if item.get("name") == SECRET_ENV and item.get("valueFrom"):
                value_from = str(item["valueFrom"])
                secret_id, key = parse_value_from(value_from)
                print(f"{name} {SECRET_ENV} from={friendly_name(value_from)}")
                return secret_id, key, value_from
        for item in container.get("environment") or []:
            if item.get("name") == SECRET_ENV and item.get("value"):
                print(f"{name} {SECRET_ENV} is a plain environment value, not a secret")
                raise SystemExit("Refusing to continue: DATABASE_URL is not stored in Secrets Manager")
    raise SystemExit("Migrate task has no DATABASE_URL secret")


def align(aws: Aws | None = None) -> None:
    client = aws or Aws()
    task = client.json(
        ["ecs", "describe-task-definition", "--task-definition", TASK_FAMILY, "--query", "taskDefinition"],
        operation="ecs:DescribeTaskDefinition",
    )
    print(f"task family={task.get('family')} revision={task.get('revision')}")
    secret_id, json_key, _value_from = find_database_url(task)
    secret_string = client.run(
        ["secretsmanager", "get-secret-value", "--secret-id", secret_id, "--query", "SecretString", "--output", "text"],
        operation="secretsmanager:GetSecretValue",
    ).rstrip("\n")
    client.remember(secret_string)
    current_url = url_from_secret(secret_string, json_key)
    client.remember(current_url)
    current_password = password_of(current_url)
    client.remember(current_password)

    described = client.json(
        ["rds", "describe-db-instances", "--db-instance-identifier", DB_INSTANCE],
        operation="rds:DescribeDBInstances",
    )
    instance = (described.get("DBInstances") or [None])[0]
    if not instance:
        raise SystemExit(f"RDS instance {DB_INSTANCE} was not found")
    master_user = instance.get("MasterUsername")
    print(f"rds instance={DB_INSTANCE} status={instance.get('DBInstanceStatus')} master_user={master_user}")
    managed = (instance.get("MasterUserSecret") or {}).get("SecretArn")
    if managed:
        print(f"rds MasterUserSecret={friendly_name(str(managed))}")
        managed_string = client.run(
            ["secretsmanager", "get-secret-value", "--secret-id", str(managed), "--query", "SecretString", "--output", "text"],
            operation="secretsmanager:GetSecretValue",
        ).rstrip("\n")
        client.remember(managed_string)
        managed_password = master_password(managed_string)
        if not managed_password:
            raise SystemExit("RDS MasterUserSecret has no password")
        client.remember(managed_password)
        if managed_password == current_password:
            print("Migrate secret already matches the RDS managed master password")
            return
        updated = secret_with_password(secret_string, json_key, managed_password)
        client.remember(updated)
        _put_secret(client, secret_id, updated)
        print("Updated the migrate DATABASE_URL secret from the RDS managed master secret")
        print("The task definition still injects DATABASE_URL. The managed secret is JSON, not a URL.")
        return

    print("RDS has no managed master secret")
    _set_rds_password(client, current_password)
    print("Set the RDS master password from the migrate secret")


def _put_secret(client: Aws, secret_id: str, secret_string: str) -> None:
    path = ""
    try:
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False)
        path = handle.name
        handle.write(secret_string)
        handle.close()
        os.chmod(path, 0o600)
        client.run(
            ["secretsmanager", "put-secret-value", "--secret-id", secret_id, "--secret-string", f"file://{path}"],
            operation="secretsmanager:PutSecretValue",
        )
    finally:
        if path and os.path.exists(path):
            os.remove(path)


def _set_rds_password(client: Aws, password: str) -> None:
    path = ""
    try:
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False)
        path = handle.name
        json.dump(
            {"DBInstanceIdentifier": DB_INSTANCE, "MasterUserPassword": password, "ApplyImmediately": True},
            handle,
        )
        handle.close()
        os.chmod(path, 0o600)
        client.run(
            ["rds", "modify-db-instance", "--cli-input-json", f"file://{path}"],
            operation="rds:ModifyDBInstance",
        )
    finally:
        if path and os.path.exists(path):
            os.remove(path)
    print("Waiting for the database to become available")
    client.run(
        ["rds", "wait", "db-instance-available", "--db-instance-identifier", DB_INSTANCE],
        operation="rds:DescribeDBInstances",
    )
    print("Database is available")


def main() -> int:
    try:
        align()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - surface the type, never a secret
        print(f"Align failed: {type(exc).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
