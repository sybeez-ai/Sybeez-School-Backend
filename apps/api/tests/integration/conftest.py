"""Integration fixtures: real FastAPI app + real PostgreSQL, rolled back after each test.

Every test runs inside one outer transaction on a dedicated connection. The request
session joins it via savepoints, so ``get_db``'s commit never reaches the database.
"""

from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from src.core.config import get_settings
from src.core.database import get_db
from src.core.security import create_access_token
from src.db.models import School, SchoolMembership, Teacher, User
from src.main import app
from src.shared.enums import UserRole


@pytest.fixture
async def db_session():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        conn = await engine.connect()
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"PostgreSQL not reachable: {exc}")
    outer = await conn.begin()
    session = AsyncSession(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        await session.close()
        await outer.rollback()
        await conn.close()
        await engine.dispose()


@pytest.fixture
async def api(db_session):
    async def _get_db():
        yield db_session
        await db_session.commit()

    app.dependency_overrides[get_db] = _get_db
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.pop(get_db, None)


@dataclass
class Actor:
    user_id: UUID
    school_id: UUID
    token: str
    teacher_id: UUID | None = None

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


@dataclass
class HrWorld:
    admin_a: Actor
    teacher_a: Actor
    teacher_a2: Actor
    admin_b: Actor
    teacher_b: Actor
    student_a: Actor


async def _school(db: AsyncSession, label: str) -> School:
    tag = uuid4().hex[:10]
    school = School(id=uuid4(), name=f"IT {label} {tag}", slug=f"it-{label.lower()}-{tag}")
    db.add(school)
    await db.flush()
    return school


async def _actor(db: AsyncSession, school: School, role: UserRole, *, teacher: bool = False) -> Actor:
    user = User(id=uuid4(), email=f"it-{uuid4().hex[:12]}@example.test", full_name=f"IT {role.value}")
    db.add(user)
    await db.flush()
    db.add(SchoolMembership(user_id=user.id, school_id=school.id, role=role.value))
    await db.flush()
    teacher_id = None
    if teacher:
        t = Teacher(id=uuid4(), school_id=school.id, user_id=user.id, teacher_code=f"IT-{uuid4().hex[:6]}")
        db.add(t)
        await db.flush()
        teacher_id = t.id
    token = create_access_token(subject=user.id, role=role.value, school_id=school.id)
    return Actor(user_id=user.id, school_id=school.id, token=token, teacher_id=teacher_id)


@pytest.fixture
async def hr_world(db_session) -> HrWorld:
    school_a = await _school(db_session, "A")
    school_b = await _school(db_session, "B")
    return HrWorld(
        admin_a=await _actor(db_session, school_a, UserRole.SCHOOL_ADMIN),
        teacher_a=await _actor(db_session, school_a, UserRole.TEACHER, teacher=True),
        teacher_a2=await _actor(db_session, school_a, UserRole.TEACHER, teacher=True),
        admin_b=await _actor(db_session, school_b, UserRole.SCHOOL_ADMIN),
        teacher_b=await _actor(db_session, school_b, UserRole.TEACHER, teacher=True),
        student_a=await _actor(db_session, school_a, UserRole.STUDENT),
    )
