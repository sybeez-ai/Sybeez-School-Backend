"""Payroll HTTP API. Existing /hr/payroll routes stay mounted separately."""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from fastapi.responses import Response as RawResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, get_current_user
from src.core.database import get_db
from src.core.exceptions import AppError
from src.modules.payroll.dashboard import PayrollDashboard
from src.modules.payroll.ops import page_rows
from src.modules.payroll.ratelimit import payroll_rate_limit
from src.modules.payroll.schemas import (
    ArrearIn,
    BankIn,
    ComplianceIn,
    ComponentCreate,
    DeclarationIn,
    DecisionIn,
    GenerateIn,
    HolidayIn,
    LoanIn,
    LoanPause,
    LoanPrepay,
    PaymentUpdate,
    PreviewIn,
    PtReplace,
    ReasonBody,
    ReimbursementIn,
    RoleGrantIn,
    RunAction,
    SalaryAssign,
    SalaryRevision,
    SettingsUpdate,
    SettlementIn,
    StatutoryProfileIn,
    TemplateCreate,
    WeeklyOffReplace,
)
from src.shared.enums import UserRole
from src.modules.payroll.permissions import PAYROLL_ROLES, payroll_access
from src.modules.payroll.service import PayrollService, _settings_dict, dump

router = APIRouter(prefix="/payroll", tags=["payroll"])

Db = Annotated[AsyncSession, Depends(get_db)]


# Employee self-service writes are open to any signed-in employee; every other write needs a payroll role.
# The check runs here, as a dependency, so a caller without payroll access gets 403 before body validation (422).
_SELF_WRITE = ("/me/", "/reimbursements")


async def _staff_write_gate(request: Request, user: CurrentUser, db: AsyncSession) -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    path = request.url.path.split("/payroll", 1)[-1]
    if path.startswith(_SELF_WRITE) and not path.startswith("/reimbursements/") or (path.startswith("/declarations/") and path.endswith("/proofs")):
        return
    await PayrollService(db, user)._allow(PAYROLL_ROLES | {UserRole.SCHOOL_ADMIN.value, UserRole.PLATFORM_ADMIN.value})


async def limited(
    request: Request,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CurrentUser:
    await payroll_rate_limit(request, user)
    await _staff_write_gate(request, user, db)
    return user


User = Annotated[CurrentUser, Depends(limited)]


def _svc(db: AsyncSession, user: CurrentUser) -> PayrollService:
    return PayrollService(db, user)


def _safe(row) -> dict | None:
    data = dump(row)
    if data and data.get("account_number"):
        data["account_last4"] = str(data["account_number"])[-4:]
        data.pop("account_number")
    return data


def _bundle(item: dict) -> dict:
    return {"template": dump(item["template"]), "lines": [dump(line) for line in item["lines"]]}


def page_query(
    q: str | None = Query(default=None, max_length=120),
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    return {"q": q, "limit": limit, "offset": offset}


Page = Annotated[dict, Depends(page_query)]


def _cut(response: Response, rows: list, page: dict, text_of) -> list:
    sliced, total = page_rows(rows, q=page["q"], limit=page["limit"], offset=page["offset"], text_of=text_of)
    response.headers["X-Total-Count"] = str(total)
    return sliced


def _blob(row) -> str:
    data = dump(row) or {}
    return " ".join(str(value) for value in data.values() if value is not None)


@router.get("/access")
async def get_access(user: User, db: Db):
    """The caller's payroll role and the actions the portal may offer."""
    if user.school_id is None:
        return {"role": "employee", "role_label": "Employee", "permissions": []}
    await _svc(db, user)._bind()
    return await payroll_access(db, user)


@router.get("/settings")
async def get_settings(user: User, db: Db):
    row = await _svc(db, user).get_settings()
    return {"id": str(row.id), **_settings_dict(row)}


@router.put("/settings")
async def update_settings(body: SettingsUpdate, user: User, db: Db):
    row = await _svc(db, user).update_settings(body.model_dump(exclude_unset=True))
    return {"id": str(row.id), **_settings_dict(row)}


@router.get("/settings/versions")
async def setting_versions(user: User, db: Db, response: Response, page: Page):
    return _cut(response, [dump(row) for row in await _svc(db, user).list_setting_versions()], page, lambda row: _blob(row))


@router.post("/settings/logo")
async def upload_logo(user: User, db: Db, file: UploadFile = File(...)):
    content = await file.read()
    row = await _svc(db, user).upload_logo(content, file.content_type)
    return {"id": str(row.id), "logo_object_key": row.logo_object_key}


@router.get("/payslip-templates")
async def payslip_templates(user: User, db: Db):
    await _svc(db, user).get_settings()
    return {
        "templates": [
            {"code": "CLASSIC", "name": "Classic"},
            {"code": "COMPACT", "name": "Compact"},
            {"code": "STATUTORY", "name": "Statutory"},
        ]
    }


@router.get("/components")
async def list_components(user: User, db: Db, response: Response, page: Page):
    return _cut(response, [dump(row) for row in await _svc(db, user).list_components()], page, lambda row: _blob(row))


@router.post("/components", status_code=201)
async def create_component(body: ComponentCreate, user: User, db: Db):
    return dump(await _svc(db, user).create_component(body.model_dump()))


@router.post("/components/{component_id}/retire")
async def retire_component(component_id: UUID, body: ReasonBody, user: User, db: Db):
    return dump(await _svc(db, user).retire_component(component_id, body.reason, None))


@router.get("/salary-templates")
async def list_templates(user: User, db: Db, response: Response, page: Page):
    return _cut(response, [_bundle(item) for item in await _svc(db, user).list_templates()], page, lambda row: _blob(row.get("template")))


@router.post("/salary-templates", status_code=201)
async def create_template(body: TemplateCreate, user: User, db: Db):
    return _bundle(await _svc(db, user).create_template(body.model_dump()))


@router.get("/salary-assignments")
async def list_salary_assignments(user: User, db: Db, response: Response, page: Page, employee_id: UUID | None = None):
    return _cut(response, await _svc(db, user).list_assignments(employee_id), page, lambda row: _blob(row))


@router.post("/salary-revisions", status_code=201)
async def revise_salary(body: SalaryRevision, user: User, db: Db):
    return dump(await _svc(db, user).revise_salary(body.model_dump()))


@router.post("/salary-assignments", status_code=201)
async def assign_salary(body: SalaryAssign, user: User, db: Db):
    return dump(await _svc(db, user).assign_salary(body.model_dump()))


@router.get("/employees/{employee_id}/profile")
async def get_profile(employee_id: UUID, user: User, db: Db):
    return dump(await _svc(db, user).get_profile(employee_id)) or {"employee_id": str(employee_id)}


@router.put("/employees/{employee_id}/profile")
async def upsert_profile(employee_id: UUID, body: StatutoryProfileIn, user: User, db: Db):
    return dump(await _svc(db, user).upsert_profile(employee_id, body.model_dump(exclude_unset=True)))


@router.put("/employees/{employee_id}/bank")
async def upsert_bank(employee_id: UUID, body: BankIn, user: User, db: Db):
    return _safe(await _svc(db, user).upsert_bank(employee_id, body.model_dump()))


@router.get("/me/profile")
async def my_profile(user: User, db: Db):
    data = await _svc(db, user).my_profile()
    return {"employee_id": str(data["employee_id"]), "profile": dump(data["profile"]), "bank": data["bank"]}


@router.get("/weekly-offs")
async def list_weekly_offs(user: User, db: Db, response: Response, page: Page):
    return _cut(response, [dump(row) for row in await _svc(db, user).list_weekly_offs()], page, lambda row: _blob(row))


@router.put("/weekly-offs")
async def replace_weekly_offs(body: WeeklyOffReplace, user: User, db: Db):
    return [dump(row) for row in await _svc(db, user).replace_weekly_offs(body.model_dump()["rules"], body.reason)]


@router.get("/holidays")
async def list_holidays(user: User, db: Db, response: Response, page: Page, year: int | None = None):
    return _cut(response, [dump(row) for row in await _svc(db, user).list_holidays(year)], page, lambda row: _blob(row))


@router.post("/holidays", status_code=201)
async def add_holiday(body: HolidayIn, user: User, db: Db):
    return dump(await _svc(db, user).add_holiday(body.model_dump()))


@router.delete("/holidays/{holiday_id}", status_code=204)
async def delete_holiday(holiday_id: UUID, user: User, db: Db, reason: str = Query(min_length=3, max_length=2000)):
    await _svc(db, user).delete_holiday(holiday_id, reason)
    return Response(status_code=204)


@router.get("/pt-slabs")
async def list_pt_slabs(user: User, db: Db, response: Response, page: Page, state: str | None = None):
    return _cut(response, [dump(row) for row in await _svc(db, user).list_pt_slabs(state)], page, lambda row: _blob(row))


@router.put("/pt-slabs/{state}")
async def replace_pt_slabs(state: str, body: PtReplace, user: User, db: Db):
    return [dump(row) for row in await _svc(db, user).replace_pt_slabs(state, [slab.model_dump() for slab in body.slabs], body.reason)]


@router.get("/loans")
async def list_loans(user: User, db: Db, response: Response, page: Page, employee_id: UUID | None = None):
    return _cut(response, [dump(row) for row in await _svc(db, user).list_loans(employee_id)], page, lambda row: _blob(row))


@router.post("/loans", status_code=201)
async def create_loan(body: LoanIn, user: User, db: Db):
    return dump(await _svc(db, user).create_loan(body.model_dump()))


@router.post("/loans/{loan_id}/pause")
async def pause_loan(loan_id: UUID, body: LoanPause, user: User, db: Db):
    return dump(await _svc(db, user).pause_loan(loan_id, body.paused_until, body.reason))


@router.get("/loans/{loan_id}/schedule")
async def loan_schedule(loan_id: UUID, user: User, db: Db):
    return await _svc(db, user).loan_schedule(loan_id)


@router.post("/loans/{loan_id}/prepay")
async def prepay_loan(loan_id: UUID, body: LoanPrepay, user: User, db: Db):
    return dump(await _svc(db, user).prepay_loan(loan_id, body.amount, body.reason))


@router.post("/loans/{loan_id}/close")
async def close_loan(loan_id: UUID, body: ReasonBody, user: User, db: Db):
    return dump(await _svc(db, user).close_loan(loan_id, body.reason))


@router.get("/arrears")
async def list_arrears(user: User, db: Db, response: Response, page: Page, employee_id: UUID | None = None, status: str | None = None):
    return _cut(response, [dump(row) for row in await _svc(db, user).list_arrears(employee_id, status)], page, lambda row: _blob(row))


@router.post("/arrears", status_code=201)
async def create_arrear(body: ArrearIn, user: User, db: Db):
    return dump(await _svc(db, user).create_arrear(body.model_dump()))


@router.get("/reimbursements")
async def list_reimbursements(user: User, db: Db, response: Response, page: Page, employee_id: UUID | None = None, status: str | None = None):
    return _cut(response, [dump(row) for row in await _svc(db, user).list_reimbursements(employee_id, status)], page, lambda row: _blob(row))


@router.post("/reimbursements", status_code=201)
async def create_reimbursement(body: ReimbursementIn, user: User, db: Db):
    return dump(await _svc(db, user).create_reimbursement(body.model_dump()))


@router.post("/reimbursements/{item_id}/decide")
async def decide_reimbursement(item_id: UUID, body: DecisionIn, user: User, db: Db):
    return dump(await _svc(db, user).decide_reimbursement(item_id, body.approve, body.reason))


@router.get("/me/declaration")
async def get_my_declaration(user: User, db: Db, financial_year: str | None = Query(default=None, max_length=9)):
    return dump(await _svc(db, user).my_declaration(financial_year))


@router.put("/me/declaration")
async def my_declaration(body: DeclarationIn, user: User, db: Db):
    return dump(await _svc(db, user).upsert_declaration(body.model_dump(), employee_id=None))


@router.get("/declarations")
async def list_declarations(
    user: User,
    db: Db,
    response: Response,
    page: Page,
    financial_year: str | None = Query(default=None, max_length=9),
    employee_id: UUID | None = None,
    status: str | None = None,
):
    return _cut(
        response,
        [dump(row) for row in await _svc(db, user).list_declarations(financial_year, employee_id, status)],
        page,
        lambda row: _blob(row),
    )


@router.put("/declarations")
async def admin_declaration(body: DeclarationIn, user: User, db: Db):
    return dump(await _svc(db, user).upsert_declaration(body.model_dump(), employee_id=body.employee_id))


@router.post("/declarations/{declaration_id}/proofs", status_code=201)
async def add_proof(declaration_id: UUID, user: User, db: Db, section: str = Query(), amount: str = Query(), note: str | None = None, file: UploadFile | None = File(None)):
    content = await file.read() if file is not None else None
    try:
        parsed = __import__("decimal").Decimal(amount)
    except Exception as exc:
        raise AppError("amount must be a number", status_code=422) from exc
    return dump(await _svc(db, user).add_proof(declaration_id, {"section": section, "amount": parsed, "note": note}, content))


@router.post("/declarations/{declaration_id}/verify")
async def verify_declaration(declaration_id: UUID, body: ReasonBody, user: User, db: Db):
    return dump(await _svc(db, user).verify_declaration(declaration_id, body.reason))


@router.get("/roles")
async def list_roles(user: User, db: Db, response: Response, page: Page):
    return _cut(response, [dump(row) for row in await _svc(db, user).list_grants()], page, lambda row: _blob(row))


@router.post("/roles", status_code=201)
async def grant_role(body: RoleGrantIn, user: User, db: Db):
    return dump(await _svc(db, user).grant_role(body.model_dump()))


@router.delete("/roles/{grant_id}", status_code=204)
async def revoke_role(grant_id: UUID, user: User, db: Db, reason: str = Query(min_length=3, max_length=2000)):
    await _svc(db, user).revoke_role(grant_id, reason)
    return Response(status_code=204)


@router.get("/audit")
async def list_audit(user: User, db: Db, response: Response, page: Page):
    rows = [dump(row) for row in await _svc(db, user).list_audit(page["limit"] or 200)]
    return _cut(response, rows, {**page, "limit": None}, lambda row: _blob(row))


@router.get("/compliance")
async def list_compliance(user: User, db: Db, response: Response, page: Page):
    return _cut(response, [dump(row) for row in await _svc(db, user).list_compliance()], page, lambda row: _blob(row))


@router.post("/compliance", status_code=201)
async def add_compliance(body: ComplianceIn, user: User, db: Db):
    return dump(await _svc(db, user).add_compliance(body.model_dump()))


@router.post("/preview")
async def preview(body: PreviewIn, user: User, db: Db):
    return await _svc(db, user).preview(body.employee_id, body.period_start, body.period_end)


@router.post("/runs/generate", status_code=201)
async def generate_run(body: GenerateIn, user: User, db: Db):
    result = await _svc(db, user).generate(body.period_start, body.period_end, source="MANUAL", reason=body.reason)
    return {"run": dump(result["run"]), "created": result["created"], "skipped": result["skipped"]}


@router.get("/runs")
async def list_runs(user: User, db: Db, response: Response, page: Page):
    return _cut(response, [dump(row) for row in await _svc(db, user).list_runs()], page, lambda row: _blob(row))


@router.post("/runs/{run_id}/recalculate")
async def recalculate_run(run_id: UUID, body: RunAction, user: User, db: Db):
    result = await _svc(db, user).recalculate_run(run_id, body.reason)
    return {"run": dump(result["run"]), "created": result["created"], "skipped": result["skipped"], "recalculated": True}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: UUID, body: RunAction, user: User, db: Db):
    return dump(await _svc(db, user).transition(run_id, "cancel", body.reason))


@router.get("/runs/{run_id}/payslips.zip")
async def payslips_zip(run_id: UUID, user: User, db: Db):
    content, name = await _svc(db, user).payslips_zip(run_id)
    return RawResponse(content=content, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/runs/{run_id}/pf-ecr")
async def pf_ecr(run_id: UUID, user: User, db: Db):
    body, mime = await _svc(db, user).pf_ecr(run_id)
    return RawResponse(content=body, media_type=mime, headers={"Content-Disposition": f'attachment; filename="pf-ecr-{run_id}.txt"'})


@router.get("/runs/{run_id}/register.csv")
async def register_csv(run_id: UUID, user: User, db: Db):
    body, mime, name = await _svc(db, user).register_file(run_id, "csv")
    return RawResponse(content=body, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/runs/{run_id}/register.xlsx")
async def register_xlsx(run_id: UUID, user: User, db: Db):
    body, mime, name = await _svc(db, user).register_file(run_id, "xlsx")
    return RawResponse(content=body, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/runs/{run_id}/bank-response")
async def bank_response(run_id: UUID, user: User, db: Db, file: UploadFile = File(...)):
    return await _svc(db, user).import_bank_response(run_id, await file.read())


@router.post("/runs/{run_id}/payments/retry")
async def retry_payments(run_id: UUID, body: RunAction, user: User, db: Db):
    if not body.reason:
        raise AppError("A reason is required to retry payments", status_code=422)
    return await _svc(db, user).retry_failed_payments(run_id, body.reason)


@router.get("/runs/{run_id}")
async def get_run(run_id: UUID, user: User, db: Db):
    result = await _svc(db, user).get_run(run_id)
    return {
        "run": dump(result["run"]),
        "payslips": [
            {"payslip": dump(item["payslip"]), "lines": [dump(line) for line in item["lines"]]} for item in result["payslips"]
        ],
    }


@router.post("/runs/{run_id}/submit")
async def submit_run(run_id: UUID, body: RunAction, user: User, db: Db):
    return dump(await _svc(db, user).transition(run_id, "submit", body.reason))


@router.post("/runs/{run_id}/approve")
async def approve_run(run_id: UUID, body: RunAction, user: User, db: Db):
    return dump(await _svc(db, user).transition(run_id, "approve", body.reason))


@router.post("/runs/{run_id}/reject")
async def reject_run(run_id: UUID, body: RunAction, user: User, db: Db):
    return dump(await _svc(db, user).transition(run_id, "reject", body.reason))


@router.post("/runs/{run_id}/lock")
async def lock_run(run_id: UUID, body: RunAction, user: User, db: Db):
    return dump(await _svc(db, user).transition(run_id, "lock", body.reason))


@router.get("/runs/{run_id}/register")
async def salary_register(run_id: UUID, user: User, db: Db):
    return await _svc(db, user).salary_register(run_id)


@router.get("/runs/{run_id}/pf-challan")
async def pf_challan(run_id: UUID, user: User, db: Db):
    return await _svc(db, user).challan(run_id, "PF")


@router.get("/runs/{run_id}/esi-challan")
async def esi_challan(run_id: UUID, user: User, db: Db):
    return await _svc(db, user).challan(run_id, "ESI")


@router.get("/runs/{run_id}/pt-report")
async def pt_report(run_id: UUID, user: User, db: Db):
    body, mime = await _svc(db, user).pt_report(run_id)
    return RawResponse(
        content=body,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="pt-{run_id}.csv"'},
    )


@router.get("/runs/{run_id}/tds-24q")
async def tds_24q(run_id: UUID, user: User, db: Db):
    body, mime = await _svc(db, user).tds_24q_report(run_id)
    return RawResponse(
        content=body,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="tds-24q-{run_id}.csv"'},
    )


@router.get("/runs/{run_id}/bank-file")
async def bank_file(run_id: UUID, user: User, db: Db):
    body, mime = await _svc(db, user).bank_file(run_id)
    return RawResponse(
        content=body,
        media_type=mime or "text/csv",
        headers={"Content-Disposition": f'attachment; filename="payroll-{run_id}.csv"'},
    )


@router.patch("/payslips/{payslip_id}/payment")
async def update_payment(payslip_id: UUID, body: PaymentUpdate, user: User, db: Db):
    return _safe(await _svc(db, user).update_payment(payslip_id, body.model_dump()))


@router.get("/payslips/me")
async def my_payslips(user: User, db: Db, response: Response, page: Page, year: int | None = None):
    return _cut(response, [dump(row) for row in await _svc(db, user).my_payslips(year)], page, lambda row: _blob(row))


@router.post("/imports/employees")
async def import_employees(user: User, db: Db, file: UploadFile = File(...)):
    return await _svc(db, user).import_employees(await file.read())


@router.post("/imports/banks")
async def import_banks(user: User, db: Db, file: UploadFile = File(...)):
    return await _svc(db, user).import_banks(await file.read())


@router.get("/exports/{kind}")
async def export_table(kind: str, user: User, db: Db, file_format: str = Query(default="csv", pattern="^(csv|xlsx)$")):
    body, mime, name = await _svc(db, user).export_table(kind, file_format)
    return RawResponse(content=body, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/payslips/{payslip_id}/pdf")
async def payslip_pdf(payslip_id: UUID, user: User, db: Db, intent: str = Query(default="download", pattern="^(view|download)$")):
    content, name = await _svc(db, user).payslip_pdf(payslip_id, intent=intent)
    disposition = "inline" if intent == "view" else "attachment"
    return RawResponse(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disposition}; filename="{name}"'},
    )


@router.get("/notifications")
async def payslip_notifications(user: User, db: Db, response: Response, page: Page):
    rows = await _svc(db, user).list_payslip_notifications(page["limit"] or 50)
    listed = [
        {
            "id": str(row.id),
            "title": row.title,
            "body": row.body,
            "category": row.category,
            "link_path": row.link_path,
            "is_read": row.is_read,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "payslip_id": str(row.entity_id) if row.entity_id else None,
        }
        for row in rows
    ]
    return _cut(response, listed, {**page, "limit": None}, lambda row: f"{row.get('title')} {row.get('body')}")


@router.get("/form16/me")
async def my_form16(user: User, db: Db, financial_year: str = Query(min_length=7, max_length=9)):
    return await _svc(db, user).form16(None, financial_year)


@router.get("/form16/me/pdf")
async def my_form16_pdf(user: User, db: Db, financial_year: str = Query(min_length=7, max_length=9)):
    content, name = await _svc(db, user).form16_pdf(None, financial_year)
    return RawResponse(content=content, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/form16/{employee_id}")
async def employee_form16(employee_id: UUID, user: User, db: Db, financial_year: str = Query(min_length=7, max_length=9)):
    return await _svc(db, user).form16(employee_id, financial_year)


@router.get("/form16/{employee_id}/pdf")
async def employee_form16_pdf(employee_id: UUID, user: User, db: Db, financial_year: str = Query(min_length=7, max_length=9)):
    content, name = await _svc(db, user).form16_pdf(employee_id, financial_year)
    return RawResponse(content=content, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/settlements", status_code=201)
async def settle(body: SettlementIn, user: User, db: Db):
    return dump(await _svc(db, user).settle(body.model_dump()))


def _dash(db: AsyncSession, user: CurrentUser) -> PayrollDashboard:
    return PayrollDashboard(_svc(db, user))


@router.get("/dashboard")
async def payroll_dashboard(user: User, db: Db, period_start: date | None = None, period_end: date | None = None):
    return await _dash(db, user).summary(period_start, period_end)


@router.get("/dashboard/period")
async def dashboard_period(user: User, db: Db, period_start: date | None = None, period_end: date | None = None):
    return await _dash(db, user).period(period_start, period_end)


@router.get("/dashboard/pipeline")
async def dashboard_pipeline(user: User, db: Db, period_start: date | None = None, period_end: date | None = None):
    return await _dash(db, user).pipeline(period_start, period_end)


@router.get("/dashboard/payslips")
async def dashboard_payslips(user: User, db: Db, period_start: date | None = None, period_end: date | None = None):
    return await _dash(db, user).payslips(period_start, period_end)


@router.get("/dashboard/cost")
async def dashboard_cost(user: User, db: Db, period_start: date | None = None, period_end: date | None = None):
    return await _dash(db, user).cost(period_start, period_end)


@router.get("/dashboard/components")
async def dashboard_components(user: User, db: Db, period_start: date | None = None, period_end: date | None = None):
    return await _dash(db, user).components(period_start, period_end)


@router.get("/dashboard/trend")
async def dashboard_trend(user: User, db: Db, period_end: date | None = None):
    return await _dash(db, user).trend(period_end)


@router.get("/dashboard/statutory")
async def dashboard_statutory(user: User, db: Db, period_start: date | None = None, period_end: date | None = None):
    return await _dash(db, user).statutory(period_start, period_end)


@router.get("/dashboard/attendance")
async def dashboard_attendance(user: User, db: Db, period_start: date | None = None, period_end: date | None = None):
    return await _dash(db, user).attendance(period_start, period_end)


@router.get("/dashboard/templates")
async def dashboard_templates(user: User, db: Db):
    return await _dash(db, user).templates()


@router.get("/dashboard/pending")
async def dashboard_pending(user: User, db: Db):
    return await _dash(db, user).pending()
