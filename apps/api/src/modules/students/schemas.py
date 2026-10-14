from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class ParentLinkInput(BaseModel):
    """Link an existing parent or create one inline."""

    parent_id: UUID | None = None
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    email: EmailStr | None = None
    phone: str | None = None
    password: str | None = Field(default=None, min_length=8)
    relationship: str = "guardian"
    is_primary: bool = False


class StudentCreate(BaseModel):
    # Identity
    first_name: str = Field(min_length=1, max_length=100)
    middle_name: str | None = Field(default=None, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str | None = Field(default=None, min_length=8)
    # Master
    admission_no: str | None = Field(default=None, max_length=64)
    date_of_birth: date | None = None
    gender: str | None = Field(default=None, max_length=32)
    blood_group: str | None = Field(default=None, max_length=16)
    nationality: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, max_length=32)
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None
    admission_date: date | None = None
    photo_url: str | None = None
    status: str | None = Field(default="ACTIVE", max_length=32)
    # Initial enrollment (optional)
    class_id: UUID | None = None
    academic_year_id: UUID | None = None
    roll_number: str | None = Field(default=None, max_length=32)
    # Parents
    parents: list[ParentLinkInput] = []


class StudentUpdate(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    middle_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    admission_no: str | None = Field(default=None, max_length=64)
    date_of_birth: date | None = None
    gender: str | None = None
    blood_group: str | None = None
    nationality: str | None = None
    phone: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None
    admission_date: date | None = None
    photo_url: str | None = None
    status: str | None = None
    is_active: bool | None = None


class StudentParentBrief(BaseModel):
    id: UUID
    user_id: UUID | None = None
    full_name: str | None = None
    email: str | None = None
    relationship: str
    phone: str | None = None
    is_primary: bool = False


class EnrollmentBrief(BaseModel):
    id: UUID
    academic_year_id: UUID
    academic_year_name: str | None = None
    grade_id: UUID | None = None
    grade_name: str | None = None
    class_id: UUID
    class_name: str | None = None
    section: str | None = None
    roll_number: str | None = None
    status: str
    enrolled_at: datetime | None = None
    left_at: datetime | None = None


class ParentCredential(BaseModel):
    email: str
    temporary_password: str


class StudentOut(BaseModel):
    id: UUID
    school_id: UUID
    user_id: UUID
    student_code: str | None = None
    admission_no: str | None = None
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    email: str | None = None
    date_of_birth: date | None = None
    gender: str | None = None
    blood_group: str | None = None
    nationality: str | None = None
    phone: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None
    admission_date: date | None = None
    photo_url: str | None = None
    status: str = "ACTIVE"
    is_active: bool = True
    created_at: datetime
    # Current (ACTIVE) placement summary
    class_id: UUID | None = None
    class_name: str | None = None
    section: str | None = None
    grade_id: UUID | None = None
    grade_name: str | None = None
    academic_year_id: UUID | None = None
    academic_year_name: str | None = None
    roll_number: str | None = None
    enrollment_id: UUID | None = None
    parents: list[StudentParentBrief] = []
    enrollments: list[EnrollmentBrief] = []
    temporary_password: str | None = None
    # Logins created for new parents in the same request (temporary passwords shown once).
    parent_credentials: list[ParentCredential] = []

    model_config = {"from_attributes": True}


class StudentPage(BaseModel):
    items: list[StudentOut]
    total: int
    page: int
    page_size: int


class StudentStatusUpdate(BaseModel):
    status: str | None = None
    is_active: bool | None = None


class BulkImportRow(BaseModel):
    row: int
    first_name: str
    middle_name: str | None = None
    last_name: str
    email: EmailStr
    admission_no: str | None = None
    date_of_birth: date | None = None
    gender: str | None = None
    academic_year: str | None = None
    grade: str | None = None
    section: str | None = None
    roll_number: str | None = None
    parent_name: str | None = None
    parent_email: EmailStr | None = None
    parent_phone: str | None = None
    relationship: str = "guardian"


class BulkImportValidateRequest(BaseModel):
    rows: list[BulkImportRow]


class BulkImportIssue(BaseModel):
    row: int
    field: str | None = None
    message: str
    level: str = "error"  # error | warning


class BulkImportValidateOut(BaseModel):
    valid_count: int
    error_count: int
    issues: list[BulkImportIssue]
    rows: list[BulkImportRow]


class BulkImportConfirmRequest(BaseModel):
    rows: list[BulkImportRow]


class BulkImportConfirmOut(BaseModel):
    created: int
    skipped: int
    errors: list[BulkImportIssue]
    students: list[StudentOut] = []
