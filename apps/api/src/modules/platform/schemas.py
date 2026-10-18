"""Extended platform SaaS schemas — append to platform module schemas."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


# --- Dashboard ---


class MetricPoint(BaseModel):
    label: str
    value: float


class AttentionItem(BaseModel):
    code: str
    message: str
    school_id: UUID | None = None
    severity: str = "warn"


class PlatformDashboard(BaseModel):
    schools_count: int = 0
    active_schools: int = 0
    trial_schools: int = 0
    suspended_schools: int = 0
    pending_invitations: int = 0
    total_students: int = 0
    total_teachers: int = 0
    total_parents: int = 0
    active_users: int = 0
    ai_requests: int = 0
    ai_tokens: int = 0
    ai_estimated_cost: float = 0
    school_growth: list[MetricPoint] = []
    user_growth: list[MetricPoint] = []
    ai_usage_trend: list[MetricPoint] = []
    attention: list[AttentionItem] = []
    recent_activity_count: int = 0


# --- Schools ---


class SchoolAdminCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str | None = Field(
        default=None,
        min_length=8,
        description="Temporary password for school admin login; generated when omitted",
    )
    phone: str | None = None


class SchoolCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    slug: str | None = Field(default=None, max_length=100, pattern=r"^[a-z0-9-]+$")
    country: str | None = None
    state: str | None = None
    city: str | None = None
    address: str | None = None
    postal_code: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    website: str | None = None
    timezone: str = "Asia/Kolkata"
    currency: str = "INR"
    plan_code: str = Field(default="starter", min_length=2, max_length=64)
    admin: SchoolAdminCreate
    # A school with the same (normalised) name already exists: the caller must confirm.
    allow_duplicate_name: bool = False


class SchoolUpdate(BaseModel):
    name: str | None = None
    status: str | None = None
    country: str | None = None
    state: str | None = None
    city: str | None = None
    address: str | None = None
    postal_code: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    website: str | None = None
    timezone: str | None = None
    currency: str | None = None
    plan_code: str | None = None


class SchoolOut(BaseModel):
    id: UUID
    name: str
    slug: str
    code: str | None
    status: str
    country: str | None
    state: str | None = None
    city: str | None
    address: str | None = None
    postal_code: str | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    timezone: str
    currency: str = "INR"
    logo_url: str | None = None
    plan_code: str | None = None
    subscription_status: str | None = None
    students_count: int = 0
    teachers_count: int = 0
    created_at: datetime
    admin_email: str | None = None
    admin_user_id: UUID | None = None
    admin_name: str | None = None
    temporary_password: str | None = None

    model_config = {"from_attributes": True}


class SchoolDetailOut(SchoolOut):
    parents_count: int = 0
    admins_count: int = 0
    entitlements: dict[str, Any] = Field(default_factory=dict)
    onboarding: dict[str, bool] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)
    subscription: dict[str, Any] | None = None


class SchoolLifecycleRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
    confirm_name: str | None = None


class InvitationCreate(BaseModel):
    email: EmailStr
    role: str = "SCHOOL_ADMIN"
    message: str | None = None
    expires_in_days: int = Field(7, ge=1, le=30)


class InvitationOut(BaseModel):
    id: UUID
    school_id: UUID
    email: EmailStr
    role: str
    status: str
    expires_at: datetime
    invite_token: str | None = None

    model_config = {"from_attributes": True}


class SchoolAdminResetPasswordRequest(BaseModel):
    # Optional: the server generates a strong temporary password when omitted (preferred).
    temporary_password: str | None = Field(default=None, min_length=8)


class SchoolAdminLoginCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str | None = Field(default=None, min_length=8)


class SchoolAdminLoginOut(BaseModel):
    user_id: UUID
    admin_email: EmailStr
    full_name: str
    temporary_password: str
    login_url: str = "/school-admin/login"


class SchoolAdminResetPasswordOut(BaseModel):
    admin_email: EmailStr
    temporary_password: str


class SchoolAdminMfaRecoveryOut(BaseModel):
    admin_email: EmailStr
    status: str = "ok"


class SchoolAdminDirectoryOut(BaseModel):
    id: UUID
    full_name: str
    email: str  # output only: never 500 a directory listing over a stored address
    school_id: UUID
    school_name: str
    role: str
    status: str
    last_login_at: datetime | None = None
    created_at: datetime


class PlatformSearchHit(BaseModel):
    type: str
    id: str
    title: str
    subtitle: str | None = None
    href: str


class PlatformSearchResult(BaseModel):
    query: str
    schools: list[PlatformSearchHit] = []
    admins: list[PlatformSearchHit] = []
    team: list[PlatformSearchHit] = []
    plans: list[PlatformSearchHit] = []


# --- Team ---


class PlatformEmployeeCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str = Field(min_length=8)
    platform_role: str = Field(default="USER")


class PlatformEmployeeUpdate(BaseModel):
    full_name: str | None = None
    is_active: bool | None = None
    platform_role: str | None = None


class PlatformEmployeeOut(BaseModel):
    id: UUID
    # Plain str: seeded / legacy accounts may use .local (EmailStr rejects reserved TLDs).
    email: str
    full_name: str
    platform_role: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None

    model_config = {"from_attributes": True}


# --- Plans ---


class PlanCreate(BaseModel):
    code: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9_-]+$")
    name: str = Field(min_length=2, max_length=120)
    description: str | None = None
    price_monthly: float = 0
    currency: str = "INR"
    billing_cycle: str = "monthly"
    max_students: int | None = None
    max_teachers: int | None = None
    max_storage_gb: int | None = None
    ai_monthly_tokens: int | None = None
    entitlements: dict[str, Any] = Field(default_factory=dict)
    is_public: bool = True
    sort_order: int = 0


class PlanUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    price_monthly: float | None = None
    currency: str | None = None
    billing_cycle: str | None = None
    max_students: int | None = None
    max_teachers: int | None = None
    max_storage_gb: int | None = None
    ai_monthly_tokens: int | None = None
    entitlements: dict[str, Any] | None = None
    is_active: bool | None = None
    is_public: bool | None = None
    sort_order: int | None = None


class PlanOut(BaseModel):
    id: UUID
    code: str
    name: str
    description: str | None = None
    price_monthly: float
    currency: str
    billing_cycle: str
    max_students: int | None = None
    max_teachers: int | None = None
    max_storage_gb: int | None = None
    ai_monthly_tokens: int | None = None
    entitlements: dict[str, Any] = Field(default_factory=dict)
    is_active: bool
    is_public: bool
    sort_order: int
    active_schools: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Settings / AI ---


class PlatformSettingsOut(BaseModel):
    platform_name: str
    support_email: str | None = None
    support_phone: str | None = None
    default_country: str | None = None
    default_timezone: str
    default_currency: str
    maintenance_mode: bool
    default_trial_days: int
    invite_emails_enabled: bool
    session_timeout_minutes: int
    max_login_attempts: int
    email_sender_name: str | None = None
    email_sender_address: str | None = None
    storage_max_file_mb: int
    extras: dict[str, Any] = Field(default_factory=dict)

    model_config = {"from_attributes": True}


class PlatformSettingsUpdate(BaseModel):
    platform_name: str | None = None
    support_email: str | None = None
    support_phone: str | None = None
    default_country: str | None = None
    default_timezone: str | None = None
    default_currency: str | None = None
    maintenance_mode: bool | None = None
    default_trial_days: int | None = None
    invite_emails_enabled: bool | None = None
    session_timeout_minutes: int | None = None
    max_login_attempts: int | None = None
    email_sender_name: str | None = None
    email_sender_address: str | None = None
    storage_max_file_mb: int | None = None
    extras: dict[str, Any] | None = None


class PlatformAiSettingsOut(BaseModel):
    default_provider: str
    fallback_provider: str | None = None
    default_chat_model: str | None = None
    fallback_chat_model: str | None = None
    request_timeout_seconds: int
    max_retries: int
    max_input_tokens: int
    max_output_tokens: int
    ai_globally_enabled: bool
    providers_json: dict[str, Any] = Field(default_factory=dict)
    models_json: list[Any] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class PlatformAiSettingsUpdate(BaseModel):
    default_provider: str | None = None
    fallback_provider: str | None = None
    default_chat_model: str | None = None
    fallback_chat_model: str | None = None
    request_timeout_seconds: int | None = None
    max_retries: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    ai_globally_enabled: bool | None = None
    providers_json: dict[str, Any] | None = None
    models_json: list[Any] | None = None


class PlatformAiOverview(BaseModel):
    ai_requests: int = 0
    ai_tokens: int = 0
    ai_estimated_cost: float = 0
    active_ai_schools: int = 0
    safety_blocks: int = 0
    provider_errors: int = 0
    by_provider: list[MetricPoint] = []
    by_model: list[MetricPoint] = []
    trend: list[MetricPoint] = []
