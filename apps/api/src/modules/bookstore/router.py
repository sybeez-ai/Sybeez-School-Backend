from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.bookstore.schemas import (
    BookstoreItemCreate,
    BookstoreItemOut,
    BookstoreItemUpdate,
    BookstoreOrderOut,
    CreateOrderIn,
    WishlistOut,
    WishlistUpdate,
)
from src.modules.bookstore.service import BookstoreService
from src.shared.enums import UserRole

router = APIRouter(prefix="/bookstore", tags=["bookstore"])


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("/health")
async def bookstore_health():
    return {"module": "bookstore", "status": "ok"}


@router.get("/items", response_model=list[BookstoreItemOut])
async def list_items(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.STUDENT, UserRole.PARENT, UserRole.SCHOOL_ADMIN, UserRole.TEACHER)),
    ],
    category: str | None = Query(None),
):
    return await BookstoreService(db).list_items(_sid(user), category=category)


@router.post("/items", status_code=201, response_model=BookstoreItemOut)
async def create_item(
    body: BookstoreItemCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await BookstoreService(db).create_item(_sid(user), body)


@router.patch("/items/{item_id}", response_model=BookstoreItemOut)
async def update_item(
    item_id: UUID,
    body: BookstoreItemUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await BookstoreService(db).update_item(_sid(user), item_id, body)


@router.delete("/items/{item_id}", status_code=204)
async def delete_item(
    item_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    await BookstoreService(db).delete_item(_sid(user), item_id)
    return None


@router.get("/wishlist", response_model=WishlistOut)
async def get_wishlist(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT))],
):
    row = await BookstoreService(db).get_wishlist(_sid(user), user.id)
    return WishlistOut(item_ids=[UUID(x) for x in row.item_ids])


@router.put("/wishlist", response_model=WishlistOut)
async def update_wishlist(
    body: WishlistUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT))],
):
    row = await BookstoreService(db).update_wishlist(_sid(user), user.id, body)
    return WishlistOut(item_ids=[UUID(x) for x in row.item_ids])


@router.post("/orders", status_code=201, response_model=BookstoreOrderOut)
async def create_order(
    body: CreateOrderIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT))],
):
    return await BookstoreService(db).create_order(_sid(user), user.id, body)


@router.get("/orders/mine", response_model=list[BookstoreOrderOut])
async def my_orders(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT))],
):
    return await BookstoreService(db).list_orders_for_student(_sid(user), user.id)


@router.get("/orders", response_model=list[BookstoreOrderOut])
async def school_orders(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await BookstoreService(db).list_orders_for_school(_sid(user))

