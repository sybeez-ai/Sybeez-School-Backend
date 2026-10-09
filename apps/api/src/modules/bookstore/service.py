from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppError, ForbiddenError, NotFoundError
from src.db.models import BookstoreItem, BookstoreOrder, BookstoreWishlist, Student
from src.modules.bookstore.schemas import BookstoreItemCreate, BookstoreItemUpdate, CreateOrderIn, WishlistUpdate
from src.modules.fees.calculations import money


class BookstoreService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _student_for_user(self, school_id: UUID, user_id: UUID) -> Student:
        st = (
            await self.db.execute(
                select(Student).where(Student.school_id == school_id, Student.user_id == user_id)
            )
        ).scalar_one_or_none()
        if not st:
            raise ForbiddenError("Student profile required")
        return st

    async def list_items(self, school_id: UUID, category: str | None = None) -> list[BookstoreItem]:
        q = select(BookstoreItem).where(BookstoreItem.school_id == school_id, BookstoreItem.is_active.is_(True))
        if category:
            q = q.where(BookstoreItem.category == category)
        rows = (await self.db.execute(q.order_by(BookstoreItem.featured.desc(), BookstoreItem.title.asc()))).scalars().all()
        return list(rows)

    async def create_item(self, school_id: UUID, data: BookstoreItemCreate) -> BookstoreItem:
        row = BookstoreItem(
            id=uuid4(),
            school_id=school_id,
            title=data.title.strip(),
            author=(data.author or "").strip(),
            category=data.category,
            price_inr=money(data.price_inr),
            rating=Decimal(str(data.rating)),
            featured=bool(data.featured),
            badge=data.badge.strip() if data.badge else None,
            is_active=bool(data.is_active),
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def update_item(self, school_id: UUID, item_id: UUID, data: BookstoreItemUpdate) -> BookstoreItem:
        row = await self.db.get(BookstoreItem, item_id)
        if not row or row.school_id != school_id:
            raise NotFoundError("Item not found")
        if data.title is not None:
            row.title = data.title.strip()
        if data.author is not None:
            row.author = (data.author or "").strip()
        if data.category is not None:
            row.category = data.category
        if data.price_inr is not None:
            row.price_inr = money(data.price_inr)
        if data.rating is not None:
            row.rating = Decimal(str(data.rating))
        if data.featured is not None:
            row.featured = bool(data.featured)
        if data.badge is not None:
            row.badge = data.badge.strip() if data.badge else None
        if data.is_active is not None:
            row.is_active = bool(data.is_active)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def delete_item(self, school_id: UUID, item_id: UUID) -> None:
        row = await self.db.get(BookstoreItem, item_id)
        if not row or row.school_id != school_id:
            raise NotFoundError("Item not found")
        row.is_active = False
        await self.db.commit()

    async def get_wishlist(self, school_id: UUID, user_id: UUID) -> BookstoreWishlist:
        st = await self._student_for_user(school_id, user_id)
        row = (
            await self.db.execute(
                select(BookstoreWishlist).where(
                    BookstoreWishlist.school_id == school_id, BookstoreWishlist.student_id == st.id
                )
            )
        ).scalar_one_or_none()
        if row:
            return row
        row = BookstoreWishlist(id=uuid4(), school_id=school_id, student_id=st.id, item_ids=[])
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def update_wishlist(self, school_id: UUID, user_id: UUID, data: WishlistUpdate) -> BookstoreWishlist:
        row = await self.get_wishlist(school_id, user_id)
        # Store as string UUIDs to keep JSON small and TS-friendly
        row.item_ids = [str(x) for x in data.item_ids]
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def create_order(self, school_id: UUID, user_id: UUID, data: CreateOrderIn) -> BookstoreOrder:
        st = await self._student_for_user(school_id, user_id)
        # Fetch and validate items
        item_ids = [x.item_id for x in data.items]
        rows = (
            await self.db.execute(
                select(BookstoreItem).where(
                    BookstoreItem.school_id == school_id,
                    BookstoreItem.id.in_(item_ids),
                    BookstoreItem.is_active.is_(True),
                )
            )
        ).scalars().all()
        by_id = {r.id: r for r in rows}
        if len(by_id) != len(set(item_ids)):
            raise AppError("One or more items are unavailable", status_code=422)

        total = Decimal("0")
        items_out: list[dict] = []
        for it in data.items:
            row = by_id[it.item_id]
            line = money(row.price_inr) * Decimal(str(it.qty))
            total += line
            items_out.append(
                {
                    "item_id": str(row.id),
                    "qty": it.qty,
                    "title": row.title,
                    "price_inr": float(row.price_inr),
                    "line_total": float(line),
                }
            )

        order = BookstoreOrder(
            id=uuid4(),
            school_id=school_id,
            student_id=st.id,
            status="PENDING",
            currency="INR",
            total_amount=money(total),
            items=items_out,
        )
        self.db.add(order)
        await self.db.commit()
        await self.db.refresh(order)
        return order

    async def list_orders_for_student(self, school_id: UUID, user_id: UUID) -> list[BookstoreOrder]:
        st = await self._student_for_user(school_id, user_id)
        rows = (
            await self.db.execute(
                select(BookstoreOrder)
                .where(BookstoreOrder.school_id == school_id, BookstoreOrder.student_id == st.id)
                .order_by(BookstoreOrder.created_at.desc())
            )
        ).scalars().all()
        return list(rows)

    async def list_orders_for_school(self, school_id: UUID) -> list[BookstoreOrder]:
        rows = (
            await self.db.execute(
                select(BookstoreOrder)
                .where(BookstoreOrder.school_id == school_id)
                .order_by(BookstoreOrder.created_at.desc())
            )
        ).scalars().all()
        return list(rows)

