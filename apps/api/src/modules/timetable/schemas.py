from datetime import datetime, time
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from src.shared.enums import PeriodKind


class TimetableSettingsOut(BaseModel):
    working_days: list[int]

    model_config = {"from_attributes": True}


class TimetableSettingsUpdate(BaseModel):
    working_days: list[int] = Field(min_length=1, max_length=7)

    @model_validator(mode="after")
    def validate_days(self):
        for d in self.working_days:
            if d < 0 or d > 6:
                raise ValueError("working_days must be 0–6 (Mon–Sun)")
        self.working_days = sorted(set(self.working_days))
        return self


class PeriodCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    kind: PeriodKind = PeriodKind.LESSON
    starts_at: time
    ends_at: time
    sort_order: int | None = None
    is_enabled: bool = True

    @model_validator(mode="after")
    def validate_range(self):
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class PeriodUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    kind: PeriodKind | None = None
    starts_at: time | None = None
    ends_at: time | None = None
    sort_order: int | None = None
    is_enabled: bool | None = None


class PeriodReorderItem(BaseModel):
    id: UUID
    sort_order: int


class PeriodReorderRequest(BaseModel):
    items: list[PeriodReorderItem] = Field(min_length=1)


class PeriodOut(BaseModel):
    id: UUID
    school_id: UUID
    name: str
    kind: str
    sort_order: int
    starts_at: time
    ends_at: time
    is_enabled: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class RoomCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    code: str | None = Field(default=None, max_length=64)
    capacity: int | None = Field(default=None, ge=1)
    is_active: bool = True


class RoomUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    code: str | None = Field(default=None, max_length=64)
    capacity: int | None = Field(default=None, ge=1)
    is_active: bool | None = None


class RoomOut(BaseModel):
    id: UUID
    school_id: UUID
    name: str
    code: str | None
    capacity: int | None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class TimetableEntryCreate(BaseModel):
    class_id: UUID
    subject_id: UUID
    teacher_id: UUID | None = None
    period_id: UUID | None = None
    academic_year_id: UUID | None = None
    day_of_week: int = Field(ge=0, le=6)
    starts_at: time | None = None
    ends_at: time | None = None
    room: str | None = Field(default=None, max_length=64)
    room_id: UUID | None = None

    @model_validator(mode="after")
    def require_period_or_times(self):
        if self.period_id is None and (self.starts_at is None or self.ends_at is None):
            raise ValueError("Provide period_id or both starts_at and ends_at")
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class TimetableEntryUpdate(BaseModel):
    class_id: UUID | None = None
    subject_id: UUID | None = None
    teacher_id: UUID | None = None
    period_id: UUID | None = None
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    starts_at: time | None = None
    ends_at: time | None = None
    room: str | None = Field(default=None, max_length=64)
    room_id: UUID | None = None


class TimetableEntryOut(BaseModel):
    id: UUID
    school_id: UUID
    academic_year_id: UUID | None = None
    class_id: UUID
    class_name: str | None = None
    subject_id: UUID
    subject_name: str | None = None
    teacher_id: UUID | None
    teacher_name: str | None = None
    period_id: UUID | None = None
    period_name: str | None = None
    period_kind: str | None = None
    day_of_week: int
    starts_at: time
    ends_at: time
    room: str | None
    room_id: UUID | None = None
    room_name: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class TeacherPeriodOut(BaseModel):
    """A scheduled period for the logged-in teacher (timetable-driven attendance)."""

    id: UUID
    class_id: UUID
    class_name: str
    subject_id: UUID
    subject_name: str
    day_of_week: int
    starts_at: time
    ends_at: time
    room: str | None = None
    period_id: UUID | None = None
    period_name: str | None = None
    slot_status: str


class GridCellOut(BaseModel):
    day_of_week: int
    period_id: UUID
    period_name: str
    period_kind: str
    starts_at: time
    ends_at: time
    entry: TimetableEntryOut | None = None


class TimetableGridOut(BaseModel):
    class_id: UUID
    class_name: str
    working_days: list[int]
    periods: list[PeriodOut]
    cells: list[GridCellOut]


class TodayItemOut(BaseModel):
    id: UUID | None = None
    starts_at: time
    ends_at: time
    period_name: str | None = None
    period_kind: str
    subject_name: str | None = None
    class_name: str | None = None
    teacher_name: str | None = None
    room: str | None = None
    class_id: UUID | None = None
    subject_id: UUID | None = None
    slot_status: str | None = None


class ChildBriefOut(BaseModel):
    student_id: UUID
    full_name: str
    class_id: UUID | None = None
    class_name: str | None = None


class TodayScheduleOut(BaseModel):
    on_date: str
    day_of_week: int
    student_id: UUID | None = None
    student_name: str | None = None
    class_name: str | None = None
    items: list[TodayItemOut]
