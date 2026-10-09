from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

BookCategory = Literal["textbooks", "reference", "fiction", "competitive", "stationery"]


class BookstoreItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    author: str
    category: str
    price_inr: Decimal
    rating: Decimal
    featured: bool
    badge: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class BookstoreItemCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    author: str = Field(default="", max_length=255)
    category: BookCategory = "textbooks"
    price_inr: Decimal = Field(ge=0)
    rating: Decimal = Field(ge=0, le=5, default=0)
    featured: bool = False
    badge: str | None = Field(default=None, max_length=64)
    is_active: bool = True


class BookstoreItemUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    author: str | None = Field(default=None, max_length=255)
    category: BookCategory | None = None
    price_inr: Decimal | None = Field(default=None, ge=0)
    rating: Decimal | None = Field(default=None, ge=0, le=5)
    featured: bool | None = None
    badge: str | None = Field(default=None, max_length=64)
    is_active: bool | None = None


class WishlistOut(BaseModel):
    item_ids: list[UUID]


class WishlistUpdate(BaseModel):
    item_ids: list[UUID] = Field(default_factory=list, max_length=200)


class OrderItemIn(BaseModel):
    item_id: UUID
    qty: int = Field(ge=1, le=50, default=1)


class CreateOrderIn(BaseModel):
    items: list[OrderItemIn] = Field(default_factory=list, min_length=1, max_length=200)


class BookstoreOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: str
    currency: str
    total_amount: Decimal
    items: list[dict]
    created_at: datetime
    updated_at: datetime

