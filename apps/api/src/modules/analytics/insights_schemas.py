"""Response models for school-admin insights (progress, heatmap, dashboard)."""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field

from src.modules.analytics.schemas import MetricPoint


class KpiValue(BaseModel):
    value: float | None = None
    previous: float | None = None
    delta: float | None = None
    unit: str = "percent"  # percent | count | currency
    marked: int | None = None
    expected: int | None = None
    unmarked: int | None = None


class ClassRank(BaseModel):
    section_id: UUID
    class_name: str
    students: int = 0
    attendance_rate: float | None = None
    average_score: float | None = None
    pass_rate: float | None = None


class TrendWeek(BaseModel):
    label: str
    week_start: date
    attendance_rate: float | None = None
    average_score: float | None = None
    fee_collection_rate: float | None = None
    fees_collected: float | None = None


class InsightsDashboardOut(BaseModel):
    from_date: date
    to_date: date
    previous_from: date
    previous_to: date
    attendance: KpiValue
    average_score: KpiValue
    pass_rate: KpiValue
    fee_collection: KpiValue
    active_students: KpiValue
    teachers: KpiValue
    currency: str = "INR"
    trends: list[TrendWeek] = Field(default_factory=list)
    subjects: list[MetricPoint] = Field(default_factory=list)
    grade_distribution: list[MetricPoint] = Field(default_factory=list)
    attendance_by_day: list[MetricPoint] = Field(default_factory=list)
    top_classes: list[ClassRank] = Field(default_factory=list)
    bottom_classes: list[ClassRank] = Field(default_factory=list)


class StudentProgressRow(BaseModel):
    student_id: UUID
    student_name: str
    section_id: UUID | None = None
    class_name: str | None = None
    attendance_rate: float | None = None
    average_score: float | None = None
    homework_completion: float | None = None
    subjects_below_focus: int = 0
    risk_reasons: list[str] = Field(default_factory=list)
    at_risk: bool = False
    trend: list[MetricPoint] = Field(default_factory=list)


class ClassProgressRow(BaseModel):
    section_id: UUID
    class_name: str
    students: int = 0
    attendance_rate: float | None = None
    average_score: float | None = None
    at_risk_count: int = 0
    trend: list[MetricPoint] = Field(default_factory=list)


class InsightsProgressOut(BaseModel):
    from_date: date
    to_date: date
    classes: list[ClassProgressRow] = Field(default_factory=list)
    students: list[StudentProgressRow] = Field(default_factory=list)
    at_risk: list[StudentProgressRow] = Field(default_factory=list)


class SubjectScore(BaseModel):
    subject_id: UUID | None = None
    subject_name: str
    average_score: float | None = None
    assessments: int = 0
    below_focus: bool = False


class StudentInsightOut(BaseModel):
    student_id: UUID
    student_name: str
    section_id: UUID | None = None
    class_name: str | None = None
    from_date: date
    to_date: date
    attendance_rate: float | None = None
    average_score: float | None = None
    pass_rate: float | None = None
    homework_completion: float | None = None
    fee_collection_rate: float | None = None
    unmarked_days: int = 0
    expected_days: int = 0
    marked_days: int = 0
    at_risk: bool = False
    risk_reasons: list[str] = Field(default_factory=list)
    subjects: list[SubjectScore] = Field(default_factory=list)
    attendance_trend: list[MetricPoint] = Field(default_factory=list)
    score_trend: list[MetricPoint] = Field(default_factory=list)


class HeatmapAxis(BaseModel):
    id: str
    label: str


class HeatmapCell(BaseModel):
    row_id: str
    column_id: str
    attendance_rate: float | None = None
    average_score: float | None = None
    marked: int = 0
    expected: int = 0
    unmarked: int = 0
    assessments: int = 0
    attendance_band: str = "no-data"
    score_band: str = "no-data"


class HeatmapLegendItem(BaseModel):
    band: str
    label: str
    min: float | None = None
    max: float | None = None


class InsightsHeatmapOut(BaseModel):
    mode: str
    from_date: date
    to_date: date
    rows: list[HeatmapAxis] = Field(default_factory=list)
    columns: list[HeatmapAxis] = Field(default_factory=list)
    cells: list[HeatmapCell] = Field(default_factory=list)
    legend: list[HeatmapLegendItem] = Field(default_factory=list)


class HeatmapDrillStudent(BaseModel):
    student_id: UUID
    student_name: str
    attendance_rate: float | None = None
    average_score: float | None = None
    marked: int = 0
    expected: int = 0
    unmarked: int = 0
    assessments: int = 0


class InsightsHeatmapDrillOut(BaseModel):
    mode: str
    class_name: str
    column_label: str
    attendance_rate: float | None = None
    average_score: float | None = None
    students: list[HeatmapDrillStudent] = Field(default_factory=list)


class ReportHistoryItem(BaseModel):
    id: UUID
    report_type: str
    action: str
    filters: dict | None = None
    created_at: datetime
    entity_id: UUID | None = None
