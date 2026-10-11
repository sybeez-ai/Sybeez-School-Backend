"""Short-lived download links for payroll reports the caller is already allowed to see."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import select

from src.core.config import get_settings
from src.core.constants import API_V1_PREFIX
from src.core.exceptions import AppError, NotFoundError
from src.core.storage import put_object, read_object
from src.db.models import PayrollRun
from src.modules.payroll.assistant.catalog import parse_uuid
from src.modules.payroll.assistant.mask import redact_text
from src.modules.payroll.reports import csv_file
from src.modules.payroll.service import PayrollService

DOWNLOAD_TTL = 600
REPORT_KINDS = (
    "payslip",
    "payslips_zip",
    "bank",
    "pf_ecr",
    "esi",
    "pt",
    "tds_24q",
    "form16",
    "register",
    "audit",
    "loans",
    "arrears",
    "reimbursements",
)


async def get_report(payroll: PayrollService, arguments: dict) -> dict:
    kind = str((arguments or {}).get("kind") or "").strip().lower()
    if kind not in REPORT_KINDS:
        raise AppError("That report is not available", status_code=422)
    data, filename, content_type = await _render(payroll, kind, arguments or {})
    payload = data if isinstance(data, bytes) else str(data).encode()
    key = f"schools/{payroll.school_id}/assistant-downloads/{uuid4()}"
    put_object(key=key, data=payload, content_type=content_type)
    url = signed_download_url(key=key, filename=filename, content_type=content_type, school_id=str(payroll.school_id))
    return {"kind": kind, "filename": filename, "expires_in": DOWNLOAD_TTL, "url": url}


def signed_download_url(*, key: str, filename: str, content_type: str, school_id: str, ttl: int = DOWNLOAD_TTL) -> str:
    settings = get_settings()
    bucket = (settings.s3_bucket or "").strip()
    if bucket:
        import boto3

        client = boto3.client("s3", region_name=settings.s3_region or None)
        return client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": bucket,
                "Key": key,
                "ResponseContentType": content_type,
                "ResponseContentDisposition": f'attachment; filename="{filename.replace(chr(34), "")}"',
            },
            ExpiresIn=ttl,
        )
    token = sign_download_token(key=key, filename=filename, content_type=content_type, school_id=school_id, ttl=ttl)
    return f"{API_V1_PREFIX}/payroll/assistant/downloads/{token}"


def sign_download_token(*, key: str, filename: str, content_type: str, school_id: str, ttl: int = DOWNLOAD_TTL) -> str:
    payload = {
        "key": key,
        "filename": filename,
        "content_type": content_type,
        "school_id": school_id,
        "exp": int(time.time()) + ttl,
    }
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signature = hmac.new(_secret(), body.encode(), hashlib.sha256).digest()
    return body + "." + _b64(signature)


def open_download_token(token: str) -> dict:
    try:
        body, signature = token.split(".", 1)
        expected = hmac.new(_secret(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(signature)):
            raise ValueError("bad signature")
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError("bad token") from exc
    if not isinstance(payload, dict) or int(payload.get("exp") or 0) < time.time():
        raise ValueError("expired")
    key = str(payload.get("key") or "")
    school_id = str(payload.get("school_id") or "")
    if not key.startswith(f"schools/{school_id}/") or ".." in key.split("/"):
        raise ValueError("bad key")
    return payload


def read_download(token: str) -> tuple[bytes, str, str]:
    try:
        payload = open_download_token(token)
        data = read_object(payload["key"])
    except (ValueError, FileNotFoundError) as exc:
        raise NotFoundError("Download not found") from exc
    return data, payload["content_type"], payload["filename"]


async def _render(payroll: PayrollService, kind: str, arguments: dict) -> tuple[bytes | str, str, str]:
    if kind == "audit":
        rows = await payroll.list_audit(500)
        body = csv_file(
            ["action", "entity_type", "entity_id", "reason", "created_at"],
            [
                [
                    row.action,
                    row.entity_type,
                    str(row.entity_id or ""),
                    redact_text(row.reason or ""),
                    row.created_at.isoformat(),
                ]
                for row in rows
            ],
        )
        return body, "audit-log.csv", "text/csv"
    if kind == "form16":
        fy = str(arguments.get("financial_year") or "").strip()
        if not fy:
            from src.modules.payroll.calc import financial_year_label

            fy = financial_year_label(date.today())
        employee_id = parse_uuid(arguments.get("employee_id"))
        data, name = await payroll.form16_pdf(employee_id, fy)
        return data, name, "application/pdf"
    if kind == "payslip":
        payslip_id = parse_uuid(arguments.get("payslip_id"))
        if payslip_id is None:
            raise AppError("A payslip id is required", status_code=422)
        data, name = await payroll.payslip_pdf(payslip_id)
        return data, name, "application/pdf"
    run_id = await _run_id(payroll, arguments)
    if kind == "payslips_zip":
        data, name = await payroll.payslips_zip(run_id)
        return data, name, "application/zip"
    if kind == "bank":
        body, mime = await payroll.bank_file(run_id)
        return body, "bank.csv", mime
    if kind == "pf_ecr":
        body, mime = await payroll.pf_ecr(run_id)
        return body, "pf-ecr.txt", mime
    if kind == "esi":
        rows = await payroll.challan(run_id, "esi")
        return (
            csv_file(
                ["employee_id", "esi_number", "employee_share", "employer_share"],
                [[row.get("employee_id"), row.get("esi_number"), row.get("employee_share"), row.get("employer_share")] for row in rows],
            ),
            "esi.csv",
            "text/csv",
        )
    if kind == "pt":
        body, mime = await payroll.pt_report(run_id)
        return body, "professional-tax.csv", mime
    if kind == "tds_24q":
        body, mime = await payroll.tds_24q_report(run_id)
        return body, "tds-24q.csv", mime
    if kind == "register":
        body, mime, name = await payroll.register_file(run_id, "csv")
        return body, name, mime
    if kind in {"loans", "arrears", "reimbursements"}:
        body, mime, name = await payroll.export_table(kind, "csv")
        return body, name, mime
    raise AppError("That report is not available", status_code=422)


async def _run_id(payroll: PayrollService, arguments: dict) -> UUID:
    parsed = parse_uuid(arguments.get("run_id"))
    if parsed is not None:
        return parsed
    start = _day(arguments.get("period_start"))
    end = _day(arguments.get("period_end"))
    if start is None or end is None:
        raise AppError("A run id or period is required", status_code=422)
    run = (
        await payroll.db.execute(
            select(PayrollRun)
            .where(
                PayrollRun.school_id == payroll.school_id,
                PayrollRun.period_start == start,
                PayrollRun.period_end == end,
                PayrollRun.approval_status.notin_(("REJECTED", "CANCELLED")),
            )
            .order_by(PayrollRun.created_at.desc())
        )
    ).scalars().first()
    if run is None:
        raise NotFoundError("Pay run not found")
    return run.id


def _day(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise AppError("A period date must look like 2026-04-01", status_code=422) from exc


def _secret() -> bytes:
    return get_settings().jwt_secret.encode()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
