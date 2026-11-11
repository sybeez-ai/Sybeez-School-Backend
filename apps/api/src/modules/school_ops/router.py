from datetime import date
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.modules.school_ops.service import SchoolOpsService
from src.shared.enums import UserRole

router = APIRouter(prefix="/ops", tags=["school-ops"])
DB = Annotated[AsyncSession, Depends(get_db)]
Admin = Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))]


class ConfirmBody(BaseModel):
    timetable_entry_id: UUID
    date: date
    substitute_teacher_id: UUID
    note: str | None = Field(default=None, max_length=500)


@router.get("/substitutes/candidates")
async def candidates(db: DB, user: Admin, teacher_id: UUID, on: date):
    return await SchoolOpsService(db).candidates(user, teacher_id, on)


@router.get("/substitutes")
async def covers(db: DB, user: Admin, on: date = Query(default_factory=date.today)):
    return await SchoolOpsService(db).list_covers(user, on)


@router.post("/substitutes", status_code=201)
async def confirm(body: ConfirmBody, db: DB, user: Admin):
    """The admin's explicit confirmation. Nothing is assigned or sent before this call."""
    row = await SchoolOpsService(db).confirm(
        user,
        entry_id=body.timetable_entry_id,
        on=body.date,
        substitute_teacher_id=body.substitute_teacher_id,
        note=body.note,
    )
    return {"id": str(row.id), "status": row.status}


@router.delete("/substitutes/{assignment_id}", status_code=204)
async def cancel(assignment_id: UUID, db: DB, user: Admin):
    await SchoolOpsService(db).cancel(user, assignment_id)


@router.get("/workload")
async def workload(db: DB, user: Admin, start: date, end: date):
    return await SchoolOpsService(db).workload(user, start, end)


@router.get("/principal-report")
async def principal_report(db: DB, user: Admin, year: int = Query(ge=2020, le=2100), month: int = Query(ge=1, le=12)):
    return await SchoolOpsService(db).monthly_report(user, year, month)


class StoreIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    category: str = "GENERAL"
    location: str | None = None
    cost: Decimal = Field(default=Decimal("0"), ge=0)
    acquired_on: date | None = None
    notes: str | None = None


class StockIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    sku: str = Field(min_length=1, max_length=64)
    quantity: Decimal = Field(default=Decimal("0"), ge=0)
    unit: str = "NOS"
    reorder_level: Decimal = Field(default=Decimal("0"), ge=0)


class OrderIn(BaseModel):
    vendor_name: str = Field(min_length=2, max_length=200)
    lines: list[dict] = Field(default_factory=list)
    total: Decimal = Field(default=Decimal("0"), ge=0)
    notes: str | None = None
    status: str = "DRAFT"


class JobIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    asset_id: UUID | None = None
    reported_on: date
    notes: str | None = None
    status: str = "OPEN"


def _dump(row) -> dict:
    out = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        if hasattr(value, "isoformat"):
            value = value.isoformat()
        elif isinstance(value, UUID):
            value = str(value)
        elif isinstance(value, Decimal):
            value = str(value)
        out[column.name] = value
    return out


@router.get("/assets")
async def list_ops_assets(db: DB, user: Admin):
    from sqlalchemy import select

    from src.db.models.books import OpsAsset

    rows = (await db.execute(select(OpsAsset).where(OpsAsset.school_id == user.school_id).order_by(OpsAsset.name))).scalars().all()
    return [_dump(r) for r in rows]


@router.post("/assets", status_code=201)
async def create_ops_asset(body: StoreIn, db: DB, user: Admin):
    from uuid import uuid4

    from src.db.models.books import OpsAsset

    row = OpsAsset(id=uuid4(), school_id=user.school_id, name=body.name.strip(), category=body.category, location=body.location, cost=body.cost, acquired_on=body.acquired_on, notes=body.notes, status="IN_USE")
    db.add(row)
    await db.flush()
    return _dump(row)


@router.get("/inventory")
async def list_inventory(db: DB, user: Admin):
    from sqlalchemy import select

    from src.db.models.books import OpsInventoryItem

    rows = (await db.execute(select(OpsInventoryItem).where(OpsInventoryItem.school_id == user.school_id).order_by(OpsInventoryItem.name))).scalars().all()
    return [_dump(r) for r in rows]


@router.post("/inventory", status_code=201)
async def create_inventory(body: StockIn, db: DB, user: Admin):
    from uuid import uuid4

    from src.db.models.books import OpsInventoryItem

    row = OpsInventoryItem(id=uuid4(), school_id=user.school_id, name=body.name.strip(), sku=body.sku.strip().upper(), quantity=body.quantity, unit=body.unit, reorder_level=body.reorder_level)
    db.add(row)
    await db.flush()
    return _dump(row)


@router.get("/procurement")
async def list_procurement(db: DB, user: Admin):
    from sqlalchemy import select

    from src.db.models.books import OpsPurchaseOrder

    rows = (await db.execute(select(OpsPurchaseOrder).where(OpsPurchaseOrder.school_id == user.school_id).order_by(OpsPurchaseOrder.created_at.desc()))).scalars().all()
    return [_dump(r) for r in rows]


@router.post("/procurement", status_code=201)
async def create_procurement(body: OrderIn, db: DB, user: Admin):
    from uuid import uuid4

    from src.db.models.books import OpsPurchaseOrder

    row = OpsPurchaseOrder(id=uuid4(), school_id=user.school_id, vendor_name=body.vendor_name.strip(), status=body.status, lines=body.lines, total=body.total, notes=body.notes)
    db.add(row)
    await db.flush()
    return _dump(row)


@router.get("/maintenance")
async def list_maintenance(db: DB, user: Admin):
    from sqlalchemy import select

    from src.db.models.books import OpsMaintenanceJob

    rows = (await db.execute(select(OpsMaintenanceJob).where(OpsMaintenanceJob.school_id == user.school_id).order_by(OpsMaintenanceJob.reported_on.desc()))).scalars().all()
    return [_dump(r) for r in rows]


@router.post("/maintenance", status_code=201)
async def create_maintenance(body: JobIn, db: DB, user: Admin):
    from uuid import uuid4

    from src.db.models.books import OpsMaintenanceJob

    row = OpsMaintenanceJob(id=uuid4(), school_id=user.school_id, asset_id=body.asset_id, title=body.title.strip(), status=body.status, reported_on=body.reported_on, notes=body.notes)
    db.add(row)
    await db.flush()
    return _dump(row)


@router.get("/principal-report.pdf")
async def principal_report_pdf(db: DB, user: Admin, year: int = Query(ge=2020, le=2100), month: int = Query(ge=1, le=12)):
    from src.modules.alerts.service import school_display_name
    from src.modules.payroll.pdf import _document

    svc = SchoolOpsService(db)
    rep = await svc.monthly_report(user, year, month)
    lines = svc.report_lines(await school_display_name(db, user.school_id), rep)
    return Response(
        _document(lines[:48], None, None),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="principal-report-{rep["month"]}.pdf"'},
    )
