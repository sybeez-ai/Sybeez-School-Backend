"""Teachers may only create homework for their own subject/section assignments
(or any subject in a section where they are the class teacher)."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.core.exceptions import ForbiddenError
from src.db.models import ClassRoom, Subject
from src.modules.homework.service import HomeworkService


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeDB:
    def __init__(self, *, classroom, subject, assignment_hit):
        self.classroom = classroom
        self.subject = subject
        self.assignment_hit = assignment_hit
        self.queries = 0

    async def get(self, model, _id):
        return self.classroom if model is ClassRoom else self.subject if model is Subject else None

    async def execute(self, _stmt):
        self.queries += 1
        return _Result(uuid4() if self.assignment_hit else None)


def _setup(*, assignment_hit: bool, class_teacher: bool):
    school_id = uuid4()
    teacher = SimpleNamespace(id=uuid4())
    classroom = SimpleNamespace(
        id=uuid4(), school_id=school_id, class_teacher_id=teacher.id if class_teacher else uuid4()
    )
    subject = SimpleNamespace(id=uuid4(), school_id=school_id)
    db = FakeDB(classroom=classroom, subject=subject, assignment_hit=assignment_hit)
    return HomeworkService(db), db, school_id, teacher, classroom, subject


async def test_assigned_subject_allowed():
    svc, db, sid, t, c, s = _setup(assignment_hit=True, class_teacher=False)
    got = await svc._assert_teacher_can_assign(school_id=sid, teacher=t, class_id=c.id, subject_id=s.id)
    assert got is c
    assert db.queries == 1


async def test_class_teacher_any_subject_allowed():
    svc, _db, sid, t, c, s = _setup(assignment_hit=False, class_teacher=True)
    got = await svc._assert_teacher_can_assign(school_id=sid, teacher=t, class_id=c.id, subject_id=s.id)
    assert got is c


async def test_unassigned_subject_in_taught_section_forbidden():
    # Previously any assignment in the section let a teacher post homework for any subject.
    svc, db, sid, t, c, s = _setup(assignment_hit=False, class_teacher=False)
    with pytest.raises(ForbiddenError):
        await svc._assert_teacher_can_assign(school_id=sid, teacher=t, class_id=c.id, subject_id=s.id)
    assert db.queries == 1
