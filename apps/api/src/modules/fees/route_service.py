"""Linked-account onboarding. Every step is audit-logged without bank numbers or PAN."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppError
from src.db.models import School
from src.db.models.fee import FeeSettings
from src.modules.fees.calculations import money
from src.modules.fees.field_crypto import encrypt_account_number, mask_account_number
from src.modules.fees.gateways.razorpay import RazorpayGateway
from src.modules.fees.gateways.razorpay_route import (
    account_payload,
    settlement_patch,
    stakeholder_payload,
)
from src.modules.fees.route_money import payment_route_mode
from src.modules.fees.route_schemas import (
    PlatformRouteUpdate,
    RouteAccountIn,
    RouteSchoolOut,
    RouteSettlementIn,
    RouteStakeholderIn,
    RouteStatusOut,
)
from src.modules.fees.route_state import (
    RouteStateError,
    assert_can_add_stakeholder,
    assert_can_configure_product,
    assert_can_create_account,
    normalize_route_status,
)
from src.modules.fees.service import FeeService
from src.modules.fees.tenant import bind_tenant, set_rls_bypass


def _status_of(body: dict, fallback: str) -> str:
    raw = body.get("activation_status") or body.get("status")
    return normalize_route_status(str(raw) if raw else None) or fallback


def _view(settings: FeeSettings, school_name: str | None = None) -> RouteStatusOut:
    mode = payment_route_mode(
        provider=settings.gateway_provider,
        route_status=settings.route_status,
        linked_account_id=settings.linked_account_id,
        allow_platform_settlement=bool(settings.allow_platform_settlement),
    )
    payload = dict(
        school_id=settings.school_id,
        linked_account_id=settings.linked_account_id,
        route_product_id=settings.route_product_id,
        stakeholder_id=settings.stakeholder_id,
        route_status=settings.route_status,
        platform_fee_percent=Decimal(str(settings.platform_fee_percent or 0)),
        platform_fee_fixed=money(settings.platform_fee_fixed) if settings.platform_fee_fixed is not None else None,
        settlement_on_hold=bool(settings.settlement_on_hold),
        settlement_hold_until=settings.settlement_hold_until,
        allow_platform_settlement=bool(settings.allow_platform_settlement),
        bank_account_last4=settings.bank_account_last4,
        bank_ifsc=settings.bank_ifsc,
        beneficiary_name=settings.beneficiary_name,
        legal_business_name=settings.legal_business_name,
        online_payments=mode if settings.gateway_provider == "razorpay" else "direct",
    )
    if school_name is None:
        return RouteStatusOut(**payload)
    return RouteSchoolOut(school_name=school_name, **payload)


class RouteService:
    def __init__(self, db: AsyncSession, gateway: RazorpayGateway | None = None) -> None:
        self.db = db
        self.fees = FeeService(db)
        self.gateway = gateway or RazorpayGateway()

    async def status(self, school_id: UUID) -> RouteStatusOut:
        settings = await self.fees.get_or_create_settings(school_id)
        await self.db.commit()
        return _view(settings)

    async def create_account(
        self, school_id: UUID, user_id: UUID, data: RouteAccountIn, *, ip: str | None = None
    ) -> RouteStatusOut:
        settings = await self.fees.get_or_create_settings(school_id)
        try:
            assert_can_create_account(settings.route_status, settings.linked_account_id)
        except RouteStateError as exc:
            raise AppError(str(exc), status_code=422) from exc
        body = await self.gateway.client().create_account(account_payload(data, str(school_id)))
        account_id = str(body.get("id") or "")
        if not account_id:
            raise AppError("Razorpay did not return a linked account id", status_code=502)
        settings.linked_account_id = account_id
        settings.route_status = _status_of(body, "created")
        settings.legal_business_name = data.legal_business_name
        if settings.gateway_provider != "razorpay":
            settings.gateway_provider = "razorpay"
        await self.fees._audit(
            school_id,
            user_id,
            "ROUTE_ACCOUNT_CREATED",
            "fee_settings",
            settings.id,
            new_values={
                "linked_account_id": account_id,
                "route_status": settings.route_status,
                "legal_business_name": data.legal_business_name,
            },
            ip_address=ip,
        )
        await self.db.commit()
        await self.db.refresh(settings)
        return _view(settings)

    async def add_stakeholder(
        self, school_id: UUID, user_id: UUID, data: RouteStakeholderIn, *, ip: str | None = None
    ) -> RouteStatusOut:
        settings = await self.fees.get_or_create_settings(school_id)
        try:
            assert_can_add_stakeholder(settings.route_status, settings.linked_account_id)
        except RouteStateError as exc:
            raise AppError(str(exc), status_code=422) from exc
        body = await self.gateway.client().create_stakeholder(
            settings.linked_account_id or "", stakeholder_payload(data)
        )
        stakeholder_id = str(body.get("id") or "")
        if not stakeholder_id:
            raise AppError("Razorpay did not return a stakeholder id", status_code=502)
        settings.stakeholder_id = stakeholder_id
        await self.fees._audit(
            school_id,
            user_id,
            "ROUTE_STAKEHOLDER_ADDED",
            "fee_settings",
            settings.id,
            new_values={"stakeholder_id": stakeholder_id, "name": data.name},
            ip_address=ip,
        )
        await self.db.commit()
        await self.db.refresh(settings)
        return _view(settings)

    async def configure_settlement(
        self, school_id: UUID, user_id: UUID, data: RouteSettlementIn, *, ip: str | None = None
    ) -> RouteStatusOut:
        settings = await self.fees.get_or_create_settings(school_id)
        try:
            assert_can_configure_product(
                settings.route_status, settings.linked_account_id, settings.stakeholder_id
            )
        except RouteStateError as exc:
            raise AppError(str(exc), status_code=422) from exc
        account_id = settings.linked_account_id or ""
        client = self.gateway.client()
        if not settings.route_product_id:
            created = await client.create_product(
                account_id, {"product_name": "route", "tnc_accepted": True}
            )
            product_id = str(created.get("id") or "")
            if not product_id:
                raise AppError("Razorpay did not return a Route product id", status_code=502)
            settings.route_product_id = product_id
            await self.fees._audit(
                school_id,
                user_id,
                "ROUTE_PRODUCT_REQUESTED",
                "fee_settings",
                settings.id,
                new_values={"route_product_id": product_id, "activation_status": _status_of(created, "created")},
                ip_address=ip,
            )
            await self.db.commit()
            await self.db.refresh(settings)
        patched = await client.update_product(
            account_id, settings.route_product_id or "", settlement_patch(data)
        )
        previous = settings.route_status
        settings.route_status = _status_of(patched, settings.route_status)
        settings.bank_account_last4 = mask_account_number(data.account_number)
        settings.bank_account_ciphertext = encrypt_account_number(data.account_number)
        settings.bank_ifsc = data.ifsc_code
        settings.beneficiary_name = data.beneficiary_name
        await self.fees._audit(
            school_id,
            user_id,
            "ROUTE_SETTLEMENT_UPDATED",
            "fee_settings",
            settings.id,
            old_values={"route_status": previous},
            new_values={
                "route_status": settings.route_status,
                "bank_account_last4": settings.bank_account_last4,
                "bank_ifsc": settings.bank_ifsc,
                "beneficiary_name": settings.beneficiary_name,
            },
            ip_address=ip,
        )
        await self.db.commit()
        await self.db.refresh(settings)
        return _view(settings)

    async def refresh(self, school_id: UUID, user_id: UUID, *, ip: str | None = None) -> RouteStatusOut:
        settings = await self.fees.get_or_create_settings(school_id)
        if not settings.linked_account_id:
            raise AppError("This school has no Razorpay linked account yet", status_code=422)
        client = self.gateway.client()
        account = await client.fetch_account(settings.linked_account_id)
        status = _status_of(account, settings.route_status)
        if settings.route_product_id:
            product = await client.fetch_product(settings.linked_account_id, settings.route_product_id)
            status = _status_of(product, status)
        previous = settings.route_status
        settings.route_status = status
        if previous != status:
            await self.fees._audit(
                school_id,
                user_id,
                "ROUTE_STATUS_CHANGED",
                "fee_settings",
                settings.id,
                old_values={"route_status": previous},
                new_values={"route_status": status, "source": "poll"},
                ip_address=ip,
            )
        await self.db.commit()
        await self.db.refresh(settings)
        return _view(settings)

    async def update_platform_fees(
        self, school_id: UUID, user_id: UUID, data: PlatformRouteUpdate, *, ip: str | None = None
    ) -> RouteStatusOut:
        await bind_tenant(self.db, school_id)
        settings = await self.fees.get_or_create_settings(school_id)
        before = {
            "platform_fee_percent": str(settings.platform_fee_percent),
            "platform_fee_fixed": str(settings.platform_fee_fixed) if settings.platform_fee_fixed is not None else None,
            "allow_platform_settlement": settings.allow_platform_settlement,
            "settlement_on_hold": settings.settlement_on_hold,
        }
        if data.platform_fee_percent is not None:
            settings.platform_fee_percent = Decimal(str(data.platform_fee_percent)).quantize(Decimal("0.0001"))
        if data.clear_platform_fee_fixed:
            settings.platform_fee_fixed = None
        elif data.platform_fee_fixed is not None:
            settings.platform_fee_fixed = money(data.platform_fee_fixed)
        if data.allow_platform_settlement is not None:
            settings.allow_platform_settlement = data.allow_platform_settlement
        if data.settlement_on_hold is not None:
            settings.settlement_on_hold = data.settlement_on_hold
        if data.clear_settlement_hold_until:
            settings.settlement_hold_until = None
        elif data.settlement_hold_until is not None:
            settings.settlement_hold_until = data.settlement_hold_until
        await self.fees._audit(
            school_id,
            user_id,
            "ROUTE_FEE_UPDATED",
            "fee_settings",
            settings.id,
            old_values=before,
            new_values={
                "platform_fee_percent": str(settings.platform_fee_percent),
                "platform_fee_fixed": (
                    str(settings.platform_fee_fixed) if settings.platform_fee_fixed is not None else None
                ),
                "allow_platform_settlement": settings.allow_platform_settlement,
                "settlement_on_hold": settings.settlement_on_hold,
            },
            ip_address=ip,
        )
        await self.db.commit()
        await self.db.refresh(settings)
        return _view(settings)

    async def list_schools(self) -> list[RouteSchoolOut]:
        await set_rls_bypass(self.db, True)
        rows = (
            await self.db.execute(
                select(FeeSettings, School.name)
                .join(School, School.id == FeeSettings.school_id)
                .order_by(School.name)
            )
        ).all()
        await set_rls_bypass(self.db, False)
        return [_view(settings, name) for settings, name in rows]  # type: ignore[return-value]


def apply_account_status(settings: FeeSettings, incoming: str | None) -> str | None:
    status = normalize_route_status(incoming)
    if status is None or status == settings.route_status:
        return None
    previous = settings.route_status
    settings.route_status = status
    return previous

