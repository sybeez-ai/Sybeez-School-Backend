from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppError, NotFoundError
from src.db.models import AdmissionApplication, AdmissionEnquiry, AdmissionStatusHistory
from src.modules.admissions.schemas import (
    AdmissionApplicationCreate,
    AdmissionApplicationOut,
    AdmissionApplicationUpdate,
    AdmissionEnquiryCreate,
    AdmissionEnquiryOut,
    AdmissionEnquiryUpdate,
    AdmitApplicationIn,
    ConvertEnquiryIn,
)
from src.modules.students.schemas import StudentCreate
from src.modules.students.service import StudentService


def _norm_status(s: str) -> str:
    return (s or "").strip().upper().replace(" ", "_")


ADMISSION_STATUSES = {"APPLIED", "UNDER_REVIEW", "OFFERED", "ADMITTED", "REJECTED", "WITHDRAWN"}
ENQUIRY_STATUSES = {"ENQUIRY", "CONVERTED", "CLOSED"}


class AdmissionsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Enquiries ─────────────────────────────────────────────

    async def create_enquiry(self, school_id: UUID, user_id: UUID, data: AdmissionEnquiryCreate) -> AdmissionEnquiryOut:
        row = AdmissionEnquiry(
            id=uuid4(),
            school_id=school_id,
            created_by=user_id,
            student_name=data.student_name.strip(),
            student_dob=data.student_dob,
            desired_grade_id=data.desired_grade_id,
            parent_name=(data.parent_name.strip() if data.parent_name else None),
            phone=(data.phone.strip() if data.phone else None),
            email=(str(data.email).strip() if data.email else None),
            notes=data.notes,
            status="ENQUIRY",
        )
        self.db.add(row)
        await self.db.flush()
        return AdmissionEnquiryOut.model_validate(row)

    async def list_enquiries(
        self,
        school_id: UUID,
        *,
        q: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AdmissionEnquiryOut]:
        qry = select(AdmissionEnquiry).where(AdmissionEnquiry.school_id == school_id)
        if status:
            st = _norm_status(status)
            qry = qry.where(AdmissionEnquiry.status == st)
        if q:
            like = f"%{q.strip()}%"
            qry = qry.where(
                or_(
                    AdmissionEnquiry.student_name.ilike(like),
                    AdmissionEnquiry.parent_name.ilike(like),
                    AdmissionEnquiry.phone.ilike(like),
                    AdmissionEnquiry.email.ilike(like),
                )
            )
        rows = (await self.db.execute(qry.order_by(AdmissionEnquiry.created_at.desc()).limit(limit).offset(offset))).scalars().all()
        return [AdmissionEnquiryOut.model_validate(r) for r in rows]

    async def get_enquiry(self, school_id: UUID, enquiry_id: UUID) -> AdmissionEnquiryOut:
        row = await self.db.get(AdmissionEnquiry, enquiry_id)
        if not row or row.school_id != school_id:
            raise NotFoundError("Enquiry not found")
        return AdmissionEnquiryOut.model_validate(row)

    async def update_enquiry(self, school_id: UUID, enquiry_id: UUID, data: AdmissionEnquiryUpdate) -> AdmissionEnquiryOut:
        row = await self.db.get(AdmissionEnquiry, enquiry_id)
        if not row or row.school_id != school_id:
            raise NotFoundError("Enquiry not found")
        if data.student_name is not None:
            row.student_name = data.student_name.strip()
        if data.student_dob is not None:
            row.student_dob = data.student_dob
        if data.desired_grade_id is not None:
            row.desired_grade_id = data.desired_grade_id
        if data.parent_name is not None:
            row.parent_name = data.parent_name.strip() if data.parent_name else None
        if data.phone is not None:
            row.phone = data.phone.strip() if data.phone else None
        if data.email is not None:
            row.email = str(data.email).strip() if data.email else None
        if data.notes is not None:
            row.notes = data.notes
        if data.status is not None:
            st = _norm_status(data.status)
            if st not in ENQUIRY_STATUSES:
                raise AppError(f"Invalid enquiry status: {st}")
            row.status = st
        await self.db.flush()
        return AdmissionEnquiryOut.model_validate(row)

    async def convert_enquiry_to_application(
        self, school_id: UUID, enquiry_id: UUID, user_id: UUID, data: ConvertEnquiryIn
    ) -> AdmissionApplicationOut:
        enquiry = await self.db.get(AdmissionEnquiry, enquiry_id)
        if not enquiry or enquiry.school_id != school_id:
            raise NotFoundError("Enquiry not found")

        tokens = (enquiry.student_name or "").strip().split()
        default_first = tokens[0] if tokens else "Student"
        default_last = " ".join(tokens[1:]).strip() if len(tokens) > 1 else "-"

        email = data.email or (enquiry.email if enquiry.email else None)
        if not email:
            raise AppError("Email is required to create an application")

        app = AdmissionApplication(
            id=uuid4(),
            school_id=school_id,
            enquiry_id=enquiry.id,
            created_by=user_id,
            first_name=(data.first_name or default_first).strip(),
            last_name=(data.last_name or default_last).strip(),
            email=str(email).strip(),
            phone=(data.phone or enquiry.phone),
            date_of_birth=(data.date_of_birth or enquiry.student_dob),
            desired_grade_id=(data.desired_grade_id or enquiry.desired_grade_id),
            status="APPLIED",
            notes=(data.notes or enquiry.notes),
        )
        self.db.add(app)
        enquiry.status = "CONVERTED"
        self.db.add(
            AdmissionStatusHistory(
                id=uuid4(),
                school_id=school_id,
                application_id=app.id,
                changed_by=user_id,
                from_status=None,
                to_status="APPLIED",
                note="Converted from enquiry",
            )
        )
        await self.db.flush()
        return AdmissionApplicationOut.model_validate(app)

    # ── Applications ──────────────────────────────────────────

    async def create_application(
        self, school_id: UUID, user_id: UUID, data: AdmissionApplicationCreate
    ) -> AdmissionApplicationOut:
        app = AdmissionApplication(
            id=uuid4(),
            school_id=school_id,
            enquiry_id=data.enquiry_id,
            created_by=user_id,
            first_name=data.first_name.strip(),
            last_name=data.last_name.strip(),
            email=str(data.email).strip(),
            phone=(data.phone.strip() if data.phone else None),
            date_of_birth=data.date_of_birth,
            desired_grade_id=data.desired_grade_id,
            status="APPLIED",
            notes=data.notes,
        )
        self.db.add(app)
        self.db.add(
            AdmissionStatusHistory(
                id=uuid4(),
                school_id=school_id,
                application_id=app.id,
                changed_by=user_id,
                from_status=None,
                to_status="APPLIED",
                note="Application created",
            )
        )
        await self.db.flush()
        return AdmissionApplicationOut.model_validate(app)

    async def list_applications(
        self,
        school_id: UUID,
        *,
        q: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AdmissionApplicationOut]:
        qry = select(AdmissionApplication).where(AdmissionApplication.school_id == school_id)
        if status:
            st = _norm_status(status)
            qry = qry.where(AdmissionApplication.status == st)
        if q:
            like = f"%{q.strip()}%"
            qry = qry.where(
                or_(
                    AdmissionApplication.first_name.ilike(like),
                    AdmissionApplication.last_name.ilike(like),
                    AdmissionApplication.email.ilike(like),
                    AdmissionApplication.phone.ilike(like),
                )
            )
        rows = (await self.db.execute(qry.order_by(AdmissionApplication.submitted_at.desc()).limit(limit).offset(offset))).scalars().all()
        return [AdmissionApplicationOut.model_validate(r) for r in rows]

    async def get_application(self, school_id: UUID, application_id: UUID) -> AdmissionApplicationOut:
        row = await self.db.get(AdmissionApplication, application_id)
        if not row or row.school_id != school_id:
            raise NotFoundError("Application not found")
        return AdmissionApplicationOut.model_validate(row)

    async def update_application(
        self, school_id: UUID, application_id: UUID, data: AdmissionApplicationUpdate
    ) -> AdmissionApplicationOut:
        row = await self.db.get(AdmissionApplication, application_id)
        if not row or row.school_id != school_id:
            raise NotFoundError("Application not found")
        if data.first_name is not None:
            row.first_name = data.first_name.strip()
        if data.last_name is not None:
            row.last_name = data.last_name.strip()
        if data.email is not None:
            row.email = str(data.email).strip()
        if data.phone is not None:
            row.phone = data.phone.strip() if data.phone else None
        if data.date_of_birth is not None:
            row.date_of_birth = data.date_of_birth
        if data.desired_grade_id is not None:
            row.desired_grade_id = data.desired_grade_id
        if data.notes is not None:
            row.notes = data.notes
        await self.db.flush()
        return AdmissionApplicationOut.model_validate(row)

    async def change_status(
        self, school_id: UUID, application_id: UUID, user_id: UUID, *, status: str, note: str | None
    ) -> AdmissionApplicationOut:
        row = await self.db.get(AdmissionApplication, application_id)
        if not row or row.school_id != school_id:
            raise NotFoundError("Application not found")
        st = _norm_status(status)
        if st not in ADMISSION_STATUSES:
            raise AppError(f"Invalid application status: {st}")
        prev = row.status
        row.status = st
        self.db.add(
            AdmissionStatusHistory(
                id=uuid4(),
                school_id=school_id,
                application_id=row.id,
                changed_by=user_id,
                from_status=prev,
                to_status=st,
                note=note,
            )
        )
        await self.db.flush()
        return AdmissionApplicationOut.model_validate(row)

    async def list_status_history(self, school_id: UUID, application_id: UUID) -> list[AdmissionStatusHistory]:
        # Validate application ownership
        row = await self.db.get(AdmissionApplication, application_id)
        if not row or row.school_id != school_id:
            raise NotFoundError("Application not found")
        q = (
            select(AdmissionStatusHistory)
            .where(
                AdmissionStatusHistory.school_id == school_id,
                AdmissionStatusHistory.application_id == application_id,
            )
            .order_by(AdmissionStatusHistory.created_at.desc())
        )
        rows = (await self.db.execute(q)).scalars().all()
        return list(rows)

    async def admit(self, school_id: UUID, application_id: UUID, user_id: UUID, data: AdmitApplicationIn):
        app = await self.db.get(AdmissionApplication, application_id)
        if not app or app.school_id != school_id:
            raise NotFoundError("Application not found")
        if _norm_status(app.status) == "ADMITTED":
            raise AppError("Application already admitted")

        first_name = (data.first_name or app.first_name).strip()
        last_name = (data.last_name or app.last_name).strip()
        email = str(data.email or app.email).strip()
        phone = (data.phone or app.phone)
        dob = data.date_of_birth or app.date_of_birth

        student = await StudentService(self.db).create(
            school_id,
            StudentCreate(
                first_name=first_name,
                last_name=last_name,
                middle_name=None,
                email=email,
                password=data.password,
                admission_no=data.admission_no,
                date_of_birth=dob,
                phone=phone,
                admission_date=data.admission_date,
                class_id=data.class_id,
                academic_year_id=data.academic_year_id,
                roll_number=data.roll_number,
                parents=data.parents,
            ),
        )

        # Mark admitted
        prev = app.status
        app.status = "ADMITTED"
        self.db.add(
            AdmissionStatusHistory(
                id=uuid4(),
                school_id=school_id,
                application_id=app.id,
                changed_by=user_id,
                from_status=prev,
                to_status="ADMITTED",
                note="Admitted → student created",
            )
        )
        await self.db.flush()
        return AdmissionApplicationOut.model_validate(app), student

