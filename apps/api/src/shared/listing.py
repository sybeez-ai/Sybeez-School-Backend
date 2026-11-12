from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.pagination import PageParams


async def fetch_page(db: AsyncSession, stmt: Select, page: PageParams):
    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    result = await db.execute(stmt.offset(page.offset).limit(page.page_size))
    return result, int(total)
