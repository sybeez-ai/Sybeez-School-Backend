"""Platform SaaS plans and entitlements."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PlatformPlan(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "platform_plans"
    __table_args__ = (UniqueConstraint("code", name="uq_platform_plans_code"),)

    code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_monthly: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")
    billing_cycle: Mapped[str] = mapped_column(String(32), nullable=False, default="monthly")
    max_students: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_teachers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_storage_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_monthly_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entitlements: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class PlatformSettings(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "platform_settings"

    # Singleton row — application always uses the first row
    platform_name: Mapped[str] = mapped_column(String(120), nullable=False, default="Sybeez")
    support_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    support_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    default_country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    default_timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    default_currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")
    maintenance_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    default_trial_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    invite_emails_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    session_timeout_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=480)
    max_login_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    email_sender_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    email_sender_address: Mapped[str | None] = mapped_column(String(320), nullable=True)
    storage_max_file_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=25)
    extras: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class PlatformAiSettings(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "platform_ai_settings"

    default_provider: Mapped[str] = mapped_column(String(64), nullable=False, default="qwen")
    fallback_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    default_chat_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    fallback_chat_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    request_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    max_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=8000)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=2048)
    ai_globally_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    providers_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    models_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)


class PlatformAiUsageDaily(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "platform_ai_usage_daily"
    __table_args__ = (
        UniqueConstraint("usage_date", "school_id", "provider", "model", "capability", name="uq_ai_usage_daily"),
    )

    usage_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    school_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    model: Mapped[str] = mapped_column(String(120), nullable=False, default="unknown")
    capability: Mapped[str] = mapped_column(String(64), nullable=False, default="CHAT")
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    safety_blocks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
