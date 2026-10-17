"""Seed helpers shared by the online-test API and RLS tests (rolled back after each test)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import create_access_token
from src.db.models import (
    AcademicYear,
    ClassRoom,
    Enrollment,
    Grade,
    Parent,
    School,
    SchoolMembership,
    Student,
    StudentParent,
    Subject,
    Teacher,
    TeacherAssignment,
    User,
)
from src.shared.enums import UserRole


@dataclass
class Person:
    user_id: UUID
    school_id: UUID
    role: str
    token: str
    profile_id: UUID | None = None

    @property
    def h(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


@dataclass
class World:
    school_id: UUID
    class_id: UUID
    class2_id: UUID
    subject_id: UUID
    admin: Person
    teacher: Person  # teaches class 1 / subject
    teacher2: Person  # teaches class 2 only
    students: list[Person] = field(default_factory=list)
    student_other_class: Person | None = None
    parent: Person | None = None  # linked to students[0]
    parent_other: Person | None = None  # linked to nobody
    other_school_id: UUID | None = None
    other_admin: Person | None = None
    other_teacher: Person | None = None


async def _person(db: AsyncSession, school_id: UUID, role: UserRole, name: str) -> Person:
    user = User(id=uuid4(), email=f"it-{uuid4().hex[:12]}@example.test", full_name=name)
    db.add(user)
    await db.flush()
    db.add(SchoolMembership(user_id=user.id, school_id=school_id, role=role.value))
    await db.flush()
    token = create_access_token(subject=user.id, role=role.value, school_id=school_id)
    return Person(user_id=user.id, school_id=school_id, role=role.value, token=token)


async def build_world(db: AsyncSession, *, n_students: int = 3) -> World:
    tag = uuid4().hex[:8]
    school = School(id=uuid4(), name=f"OT {tag}", slug=f"ot-{tag}")
    other = School(id=uuid4(), name=f"OT other {tag}", slug=f"ot-o-{tag}")
    db.add_all([school, other])
    await db.flush()
    year = AcademicYear(id=uuid4(), school_id=school.id, name="2026-27", starts_on=date(2026, 4, 1),
                        ends_on=date(2027, 3, 31), is_current=True)
    grade = Grade(id=uuid4(), school_id=school.id, name="Class 5", level=5)
    db.add_all([year, grade])
    await db.flush()
    room = ClassRoom(id=uuid4(), school_id=school.id, grade_id=grade.id, academic_year_id=year.id, name="5-A", section="A")
    room2 = ClassRoom(id=uuid4(), school_id=school.id, grade_id=grade.id, academic_year_id=year.id, name="5-B", section="B")
    subject = Subject(id=uuid4(), school_id=school.id, name="Maths", code=f"M{tag}")
    db.add_all([room, room2, subject])
    await db.flush()

    admin = await _person(db, school.id, UserRole.SCHOOL_ADMIN, "OT Admin")
    teacher = await _person(db, school.id, UserRole.TEACHER, "OT Teacher")
    teacher2 = await _person(db, school.id, UserRole.TEACHER, "OT Teacher Two")
    for person, room_ in ((teacher, room), (teacher2, room2)):
        t = Teacher(id=uuid4(), school_id=school.id, user_id=person.user_id, status="ACTIVE")
        db.add(t)
        await db.flush()
        person.profile_id = t.id
        db.add(TeacherAssignment(id=uuid4(), school_id=school.id, teacher_id=t.id, class_id=room_.id,
                                 subject_id=subject.id, academic_year_id=year.id))

    async def enrol(person: Person, room_: ClassRoom) -> None:
        s = Student(id=uuid4(), school_id=school.id, user_id=person.user_id, status="ACTIVE")
        db.add(s)
        await db.flush()
        person.profile_id = s.id
        db.add(Enrollment(id=uuid4(), school_id=school.id, student_id=s.id, class_id=room_.id,
                          academic_year_id=year.id, status="ACTIVE"))

    students = []
    for i in range(n_students):
        p = await _person(db, school.id, UserRole.STUDENT, f"OT Student {i}")
        await enrol(p, room)
        students.append(p)
    s_other = await _person(db, school.id, UserRole.STUDENT, "OT Student Other")
    await enrol(s_other, room2)

    parent = await _person(db, school.id, UserRole.PARENT, "OT Parent")
    p_row = Parent(id=uuid4(), school_id=school.id, user_id=parent.user_id)
    parent_other = await _person(db, school.id, UserRole.PARENT, "OT Parent Two")
    p2_row = Parent(id=uuid4(), school_id=school.id, user_id=parent_other.user_id)
    db.add_all([p_row, p2_row])
    await db.flush()
    parent.profile_id = p_row.id
    parent_other.profile_id = p2_row.id
    db.add(StudentParent(id=uuid4(), school_id=school.id, student_id=students[0].profile_id, parent_id=p_row.id))

    other_admin = await _person(db, other.id, UserRole.SCHOOL_ADMIN, "OT Other Admin")
    other_teacher = await _person(db, other.id, UserRole.TEACHER, "OT Other Teacher")
    ot = Teacher(id=uuid4(), school_id=other.id, user_id=other_teacher.user_id, status="ACTIVE")
    db.add(ot)
    await db.flush()
    other_teacher.profile_id = ot.id
    return World(
        school_id=school.id, class_id=room.id, class2_id=room2.id, subject_id=subject.id, admin=admin,
        teacher=teacher, teacher2=teacher2, students=students, student_other_class=s_other, parent=parent,
        parent_other=parent_other, other_school_id=other.id, other_admin=other_admin, other_teacher=other_teacher,
    )
