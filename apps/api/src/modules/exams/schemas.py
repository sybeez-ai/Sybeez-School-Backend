from datetime import date, datetime, time
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.shared.enums import ExamAttendanceStatus, ExamStatus, MarkEntryStatus


class ExamTypeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    is_active: bool = True


class ExamTypeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    is_active: bool | None = None


class ExamTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    name: str
    description: str | None
    is_active: bool


class GradeRuleIn(BaseModel):
    grade: str = Field(min_length=1, max_length=16)
    min_percentage: float = Field(ge=0, le=100)
    max_percentage: float = Field(ge=0, le=100)
    description: str | None = None
    sort_order: int = 0

    @model_validator(mode="after")
    def _range(self):
        if self.min_percentage > self.max_percentage:
            raise ValueError("min_percentage cannot exceed max_percentage")
        return self


class GradingScaleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    is_default: bool = False
    is_active: bool = True
    must_pass_all_subjects: bool = True
    rules: list[GradeRuleIn] = Field(default_factory=list)


class GradingScaleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    is_default: bool | None = None
    is_active: bool | None = None
    must_pass_all_subjects: bool | None = None
    rules: list[GradeRuleIn] | None = None


class GradeRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    grade: str
    min_percentage: float
    max_percentage: float
    description: str | None
    sort_order: int


class GradingScaleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    name: str
    is_default: bool
    is_active: bool = True
    must_pass_all_subjects: bool
    rules: list[GradeRuleOut] = []


class ExamCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    academic_year_id: UUID
    exam_type_id: UUID | None = None
    grade_id: UUID | None = None
    class_ids: list[UUID] = Field(default_factory=list)
    grading_scale_id: UUID | None = None
    starts_on: date | None = None
    ends_on: date | None = None
    must_pass_all_subjects: bool = True
    status: ExamStatus = ExamStatus.DRAFT

    @model_validator(mode="after")
    def _dates(self):
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValueError("ends_on cannot be before starts_on")
        return self


class ExamUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    exam_type_id: UUID | None = None
    grade_id: UUID | None = None
    class_ids: list[UUID] | None = None
    grading_scale_id: UUID | None = None
    starts_on: date | None = None
    ends_on: date | None = None
    must_pass_all_subjects: bool | None = None
    status: ExamStatus | None = None


class ExamSubjectCreate(BaseModel):
    subject_id: UUID
    grade_id: UUID | None = None
    max_marks: float = Field(default=100, gt=0)
    pass_marks: float = Field(default=35, ge=0)
    duration_minutes: int | None = Field(default=None, gt=0)
    instructions: str | None = None
    exam_date: date | None = None
    starts_at: time | None = None
    ends_at: time | None = None

    @model_validator(mode="after")
    def _pass(self):
        if self.pass_marks > self.max_marks:
            raise ValueError("pass_marks cannot exceed max_marks")
        return self


class ExamSubjectUpdate(BaseModel):
    max_marks: float | None = Field(default=None, gt=0)
    pass_marks: float | None = Field(default=None, ge=0)
    duration_minutes: int | None = Field(default=None, gt=0)
    instructions: str | None = None
    exam_date: date | None = None
    starts_at: time | None = None
    ends_at: time | None = None


class ExamSubjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    exam_id: UUID
    subject_id: UUID
    subject_name: str | None = None
    grade_id: UUID | None
    max_marks: float
    pass_marks: float
    duration_minutes: int | None
    instructions: str | None = None
    exam_date: date | None
    starts_at: time | None
    ends_at: time | None


class ExamScheduleCreate(BaseModel):
    exam_subject_id: UUID
    class_id: UUID | None = None
    exam_date: date
    starts_at: time | None = None
    ends_at: time | None = None
    room: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _times(self):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("start_time must be before end_time")
        return self


class ExamScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    exam_id: UUID
    exam_subject_id: UUID
    subject_id: UUID | None = None
    subject_name: str | None = None
    class_id: UUID | None
    exam_date: date
    starts_at: time | None
    ends_at: time | None
    room: str | None


class StudentExamScheduleItemOut(BaseModel):
    exam_id: UUID
    exam_title: str
    exam_type_name: str | None = None
    subject_id: UUID | None = None
    subject_name: str | None = None
    class_id: UUID | None = None
    class_name: str | None = None
    exam_date: date
    starts_at: time | None = None
    ends_at: time | None = None
    room: str | None = None
    duration_minutes: int | None = None


class ExamSectionOut(BaseModel):
    class_id: UUID
    class_name: str | None = None
    section: str | None = None


class ExamOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    academic_year_id: UUID | None
    exam_type_id: UUID | None
    exam_type_name: str | None = None
    grade_id: UUID | None
    grade_name: str | None = None
    grading_scale_id: UUID | None
    title: str
    description: str | None
    starts_on: date | None
    ends_on: date | None
    status: str
    must_pass_all_subjects: bool
    published_at: datetime | None
    locked: bool
    sections: list[ExamSectionOut] = []
    subjects: list[ExamSubjectOut] = []
    schedules: list[ExamScheduleOut] = []


class MarkRowIn(BaseModel):
    student_id: UUID
    obtained_marks: float | None = None
    attendance: ExamAttendanceStatus = ExamAttendanceStatus.PRESENT
    remarks: str | None = None

    @field_validator("obtained_marks")
    @classmethod
    def _non_neg(cls, v: float | None) -> float | None:
        if v is not None and v < 0:
            raise ValueError("marks cannot be negative")
        return v


class MarksSaveIn(BaseModel):
    exam_subject_id: UUID
    class_id: UUID
    rows: list[MarkRowIn]


class MarkRowOut(BaseModel):
    id: UUID | None = None
    student_id: UUID
    student_name: str | None = None
    roll_number: str | None = None
    obtained_marks: float | None
    max_marks: float
    attendance: str
    grade: str | None
    remarks: str | None
    status: str


class MarksSheetOut(BaseModel):
    exam_id: UUID
    exam_title: str
    exam_subject_id: UUID
    subject_id: UUID
    subject_name: str | None
    class_id: UUID
    class_name: str | None
    max_marks: float
    pass_marks: float
    entry_status: str
    locked: bool
    rows: list[MarkRowOut]


class TeacherExamOut(BaseModel):
    exam_id: UUID
    exam_title: str
    exam_status: str
    exam_subject_id: UUID
    subject_id: UUID
    subject_name: str | None
    class_id: UUID
    class_name: str | None
    max_marks: float
    pass_marks: float
    entry_status: str
    student_count: int = 0
    marks_entered: int = 0
    starts_on: date | None
    ends_on: date | None


class UnlockMarksIn(BaseModel):
    exam_subject_id: UUID
    class_id: UUID
    reason: str = Field(min_length=1, max_length=500)


class MarksCorrectionIn(BaseModel):
    student_id: UUID
    exam_subject_id: UUID
    obtained_marks: float | None = None
    attendance: ExamAttendanceStatus | None = None
    reason: str = Field(min_length=1, max_length=500)


class PublishResultsIn(BaseModel):
    allow_incomplete: bool = False


class CalculateResultsOut(BaseModel):
    exam_id: UUID
    status: str
    results_count: int
    incomplete_count: int = 0
    passed: int = 0
    failed: int = 0


class SubjectResultOut(BaseModel):
    exam_subject_id: UUID
    subject_id: UUID
    subject_name: str | None
    obtained_marks: float | None
    max_marks: float
    pass_marks: float
    attendance: str
    grade: str | None
    passed: bool | None = None


class StudentResultOut(BaseModel):
    exam_id: UUID
    exam_title: str
    student_id: UUID
    student_name: str | None = None
    class_id: UUID | None = None
    class_name: str | None = None
    roll_number: str | None = None
    admission_no: str | None = None
    total_obtained: float
    total_max: float
    percentage: float | None
    grade_label: str | None
    pass_fail: str | None
    is_published: bool
    published_at: datetime | None = None
    subjects: list[SubjectResultOut] = []


class ChildBriefOut(BaseModel):
    student_id: UUID
    full_name: str
    class_name: str | None = None


class ExamOverviewSection(BaseModel):
    class_id: UUID
    class_name: str | None
    student_count: int
    marks_submitted: int
    marks_expected: int


class ExamOverviewOut(BaseModel):
    exam_id: UUID
    title: str
    status: str
    locked: bool
    published: bool
    student_count: int
    marks_complete: int = 0
    passed: int = 0
    failed: int = 0
    average: float | None
    highest: float | None
    lowest: float | None
    pass_percent: float | None
    sections: list[ExamOverviewSection] = []


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    exam_id: UUID
    student_id: UUID
    exam_subject_id: UUID | None
    old_marks: float | None
    new_marks: float | None
    changed_by: UUID | None
    reason: str | None
    created_at: datetime


class PublishOut(BaseModel):
    exam_id: UUID
    status: str
    results_count: int
    published_at: datetime
    passed: int = 0
    failed: int = 0
