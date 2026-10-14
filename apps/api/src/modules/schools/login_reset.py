"""Admin "reset / resend login" for student, teacher and parent accounts."""

from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import NotFoundError
from src.db.models import Parent, Student, Teacher, User
from src.modules.access.audit import write_audit
from src.modules.schools.provisioning import issue_temporary_password


class LoginCredentialsOut(BaseModel):
    """Shown once to the admin; the user must change the password at first sign-in."""

    user_id: UUID
    email: str
    temporary_password: str
    must_change_password: bool = True
    login_path: str


_LOGIN_PATH = {Student: "/student/login", Teacher: "/teacher/login", Parent: "/parent/login"}


async def reset_login(
    db: AsyncSession,
    *,
    school_id: UUID,
    model: type[Student] | type[Teacher] | type[Parent],
    entity_id: UUID,
    actor_id: UUID | None,
) -> LoginCredentialsOut:
    row = (
        await db.execute(select(model).where(model.id == entity_id, model.school_id == school_id))
    ).scalar_one_or_none()
    if row is None or row.user_id is None:
        raise NotFoundError(f"{model.__name__} not found")
    user = (await db.execute(select(User).where(User.id == row.user_id))).scalar_one_or_none()
    if user is None:
        raise NotFoundError("Login account not found")
    temp = await issue_temporary_password(db, user=user)
    await write_audit(
        db,
        school_id=school_id,
        actor_id=actor_id,
        action="login.reset",
        resource_type="user",
        resource_id=str(user.id),
        message=f"Temporary password issued for {model.__name__.lower()} login",
        details={"entity": model.__name__.lower(), "entity_id": str(entity_id)},
    )
    return LoginCredentialsOut(
        user_id=user.id, email=user.email, temporary_password=temp, login_path=_LOGIN_PATH[model]
    )
