"""Teaching records: teachers write/read only their own sections; topics must match the subject."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.core.exceptions import ForbiddenError, NotFoundError
from src.modules.teaching_records.schemas import TeachingRecordCreate, TeachingRecordOut
from src.modules.teaching_records.service import TeachingRecordService


class _R:
    def __init__(self, v):
        self.v = v

    def scalar_one_or_none(self):
        return self.v


class FakeDB:
    def __init__(self, classroom, results):
        self.classroom = classroom
        self.results = list(results)

    async def get(self, _model, _id):
        return self.classroom

    async def execute(self, _stmt):
        return _R(self.results.pop(0))


def _ctx(*, class_teacher=False, results=()):
    sid = uuid4()
    teacher = SimpleNamespace(id=uuid4())
    room = SimpleNamespace(id=uuid4(), school_id=sid, class_teacher_id=teacher.id if class_teacher else None)
    return TeachingRecordService(FakeDB(room, results)), sid, teacher, room


async def test_assigned_teacher_allowed():
    svc, sid, t, room = _ctx(results=[uuid4()])
    await svc.assert_teaches(school_id=sid, teacher=t, class_id=room.id, subject_id=uuid4())


async def test_class_teacher_allowed_without_assignment():
    svc, sid, t, room = _ctx(class_teacher=True)
    await svc.assert_teaches(school_id=sid, teacher=t, class_id=room.id, subject_id=uuid4())


async def test_unassigned_teacher_forbidden():
    svc, sid, t, room = _ctx(results=[None])
    with pytest.raises(ForbiddenError):
        await svc.assert_teaches(school_id=sid, teacher=t, class_id=room.id)


async def test_other_school_section_not_found():
    svc, _sid, t, room = _ctx()
    with pytest.raises(NotFoundError):
        await svc.assert_teaches(school_id=uuid4(), teacher=t, class_id=room.id)


async def test_topic_subject_mismatch_forbidden():
    subj = uuid4()
    svc, sid, _t, _room = _ctx(results=[uuid4()])
    with pytest.raises(ForbiddenError):
        await svc._validate_topic(school_id=sid, subject_id=subj, topic_id=uuid4())
    svc, sid, _t, _room = _ctx(results=[subj])
    await svc._validate_topic(school_id=sid, subject_id=subj, topic_id=uuid4())


def test_schema_carries_syllabus_topic():
    tid = uuid4()
    body = TeachingRecordCreate(
        class_id=uuid4(), subject_id=uuid4(), taught_on="2026-10-12", topic="T", syllabus_topic_id=tid
    )
    assert body.syllabus_topic_id == tid
    assert "syllabus_topic_id" in TeachingRecordOut.model_fields
